using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using LiveOps;
using Xunit;

namespace LiveOps.Tests
{
    public class ClientTests
    {
        private DateTime _now = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        private (LiveOpsClient, FakeTransport, MemoryStore) Make(Action<LiveOpsOptions> configure = null)
        {
            var t = new FakeTransport();
            var store = new MemoryStore();
            var o = new LiveOpsOptions
            {
                BaseUrl = "https://api.test/", ApiKey = "lo_client_x", Transport = t, Store = store,
                UtcNow = () => _now,
            };
            configure?.Invoke(o);
            return (new LiveOpsClient(o), t, store);
        }

        [Fact]
        public async Task Login_PersistsTokenAndGeneratedDeviceId()
        {
            var (c, t, store) = Make();
            t.EnqueueLogin();
            var r = await c.LoginAsync();

            Assert.Equal("p1", r.PlayerId);
            Assert.Equal("p1", c.PlayerId);
            Assert.True(c.HasValidToken);
            var req = t.Requests[0];
            Assert.Equal("https://api.test/v1/auth/anonymous", req.Url);
            Assert.Equal("lo_client_x", req.Headers["X-Api-Key"]);
            Assert.Contains(store.Get("liveops.install_id"), req.Body);

            // Device id is stable across client instances sharing the store.
            var c2 = new LiveOpsClient(new LiveOpsOptions { BaseUrl = "x", ApiKey = "k", Transport = t, Store = store });
            Assert.Equal(c.DeviceId, c2.DeviceId);
        }

        [Fact]
        public async Task ExplicitDeviceIdIsUsed()
        {
            var (c, t, _) = Make(o => o.DeviceId = "my-device-123");
            t.EnqueueLogin();
            await c.LoginAsync();
            Assert.Contains("my-device-123", t.Requests[0].Body);
        }

        [Fact]
        public async Task PlayerCall_LogsInAutomatically_AndRefreshesNearExpiry()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin("t1", expiresIn: 120).Enqueue(200, "[]");
            await c.CloudSave.ListAsync();
            Assert.Equal("Bearer t1", t.Requests[1].Headers["Authorization"]);

            _now = _now.AddSeconds(90); // within the 60s refresh margin
            t.EnqueueLogin("t2").Enqueue(200, "[]");
            await c.CloudSave.ListAsync();
            Assert.Equal(4, t.Requests.Count);
            Assert.Equal("Bearer t2", t.Requests[3].Headers["Authorization"]);
        }

        [Fact]
        public async Task Unauthorized_ReLoginsOnceAndRetries()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin("t1").Enqueue(401).EnqueueLogin("t2").Enqueue(200, "[]");
            await c.CloudSave.ListAsync();
            Assert.Equal("Bearer t2", t.Requests[3].Headers["Authorization"]);

            t.Enqueue(401).EnqueueLogin("t3").Enqueue(401);
            var ex = await Assert.ThrowsAsync<LiveOpsException>(() => c.CloudSave.ListAsync());
            Assert.Equal(401, ex.Status);
        }

        [Fact]
        public async Task ConcurrentCallsShareOneLogin()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin().Enqueue(200, "[]").Enqueue(200, "[]");
            await Task.WhenAll(c.CloudSave.ListAsync(), c.CloudSave.ListAsync());
            Assert.Single(t.Requests, r => r.Url.EndsWith("/v1/auth/anonymous"));
        }

        [Fact]
        public async Task Config_FetchEtagAndOfflineFallback()
        {
            var (c, t, store) = Make(o => o.Attributes["platform"] = "ios");
            RemoteConfig raised = null;
            c.ConfigUpdated += cfg => raised = cfg;

            t.EnqueueLogin().Enqueue(200,
                "{\"version\":2,\"values\":{\"lives\":3,\"shop\":{\"discount\":0.25}},\"flags\":{\"new_shop\":true}}",
                new Dictionary<string, string> { ["etag"] = "\"abc\"" });
            var cfg = await c.FetchConfigAsync();
            Assert.Equal("https://api.test/v1/config?platform=ios", t.Requests[1].Url);
            Assert.Same(cfg, raised);
            Assert.Equal(3, cfg.Get<int>("lives"));
            Assert.Equal(0.25, cfg.Get<double>("shop.discount"));
            Assert.Equal("def", cfg.Get("missing.key", "def"));
            Assert.Equal(7, cfg.Get("shop", 7)); // object -> int fails -> fallback
            Assert.True(cfg.IsEnabled("new_shop"));
            Assert.False(cfg.IsEnabled("unknown"));

            t.Enqueue(304);
            raised = null;
            var same = await c.FetchConfigAsync();
            Assert.Equal("\"abc\"", t.Requests[2].Headers["If-None-Match"]);
            Assert.Same(cfg, same);
            Assert.Null(raised);

            t.EnqueueNetworkError();
            Assert.Equal(2, (await c.FetchConfigAsync()).Version);

            // A new client starts with the cached config before any network call.
            var c2 = new LiveOpsClient(new LiveOpsOptions { BaseUrl = "x", ApiKey = "k", Transport = t, Store = store });
            Assert.Equal(2, c2.Config.Version);
            Assert.True(c2.Config.IsEnabled("new_shop"));
        }

        [Fact]
        public async Task Config_EtagDroppedWhenAttributesChange()
        {
            var attrs = new Dictionary<string, string> { ["platform"] = "ios" };
            var (c, t, _) = Make(o => o.Attributes = attrs);
            t.EnqueueLogin().Enqueue(200, "{\"version\":1,\"values\":{},\"flags\":{}}",
                new Dictionary<string, string> { ["etag"] = "\"e1\"" });
            await c.FetchConfigAsync();
            attrs["app_version"] = "1.2.0";
            t.Enqueue(200, "{\"version\":1,\"values\":{},\"flags\":{}}");
            await c.FetchConfigAsync();
            Assert.False(t.Requests[2].Headers.ContainsKey("If-None-Match"));
            Assert.Equal("https://api.test/v1/config?app_version=1.2.0&platform=ios", t.Requests[2].Url);
        }

        [Fact]
        public async Task Errors_AreMapped()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin().Enqueue(429, "{\"detail\":\"rate limit exceeded\"}",
                new Dictionary<string, string> { ["retry-after"] = "12" });
            var rl = await Assert.ThrowsAsync<RateLimitedException>(() => c.Leaderboards.GetTopAsync("main"));
            Assert.Equal(TimeSpan.FromSeconds(12), rl.RetryAfter);

            t.Enqueue(404, "{\"detail\":\"leaderboard not found\"}");
            var nf = await Assert.ThrowsAsync<LiveOpsException>(() => c.Leaderboards.GetTopAsync("x"));
            Assert.Equal(404, nf.Status);
            Assert.Contains("leaderboard not found", nf.Message);

            t.EnqueueNetworkError();
            await Assert.ThrowsAsync<NetworkException>(() => c.Leaderboards.GetTopAsync("main"));
        }

        [Fact]
        public async Task Leaderboards_BuildUrlsAndParse()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin().Enqueue(200, "{\"season\":\"all\",\"score\":500,\"rank\":1}");
            var s = await c.Leaderboards.SubmitScoreAsync("main", 500);
            Assert.Equal(500, s.Score);
            Assert.Equal("{\"score\":500}", t.Requests[1].Body);

            t.Enqueue(200, "{\"key\":\"main\",\"season\":\"2026-W01\",\"total\":1,\"entries\":[{\"rank\":1,\"player_id\":\"p1\",\"score\":500}]}");
            var page = await c.Leaderboards.GetTopAsync("main", limit: 5, offset: 10, season: "previous");
            Assert.Equal("https://api.test/v1/leaderboards/main?limit=5&offset=10&season=previous", t.Requests[2].Url);
            Assert.Equal(500, page.Entries[0].Score);

            t.Enqueue(200, "{\"key\":\"main\",\"season\":\"all\",\"total\":0,\"me\":null,\"around\":[]}");
            var me = await c.Leaderboards.GetAroundMeAsync("main", radius: 2);
            Assert.Null(me.Me);
        }

        private sealed class Progress { public int Level; public List<string> Items = new List<string>(); }

        [Fact]
        public async Task CloudSave_RoundTripAndConflicts()
        {
            var (c, t, _) = Make();
            t.EnqueueLogin().Enqueue(404, "{\"detail\":\"save not found\"}");
            Assert.Null(await c.CloudSave.LoadAsync<Progress>("main"));

            t.Enqueue(200, "{\"slot\":\"main\",\"version\":1,\"size_bytes\":10,\"created_at\":\"2026-01-01T00:00:00Z\",\"updated_at\":\"2026-01-01T00:00:00Z\"}");
            var info = await c.CloudSave.SaveAsync("main", new Progress { Level = 3 }, expectedVersion: 0);
            Assert.Equal(1, info.Version);
            Assert.Equal("PUT", t.Requests[2].Method);
            Assert.Contains("\"expected_version\":0", t.Requests[2].Body);
            Assert.Contains("\"Level\":3", t.Requests[2].Body);

            t.Enqueue(200, "{\"slot\":\"main\",\"version\":1,\"size_bytes\":10,\"updated_at\":\"2026-01-01T00:00:00Z\",\"data\":{\"Level\":3,\"Items\":[\"sword\"]}}");
            var loaded = await c.CloudSave.LoadAsync<Progress>("main");
            Assert.Equal(3, loaded.Data.Level);
            Assert.Equal("sword", loaded.Data.Items[0]);

            t.Enqueue(409, "{\"detail\":{\"message\":\"version mismatch\",\"current_version\":4}}");
            var conflict = await Assert.ThrowsAsync<SaveConflictException>(
                () => c.CloudSave.SaveAsync("main", new Progress(), expectedVersion: 1));
            Assert.Equal(4, conflict.CurrentVersion);

            t.Enqueue(409, "{\"detail\":{\"message\":\"slot limit (10) reached\"}}");
            var limit = await Assert.ThrowsAsync<LiveOpsException>(() => c.CloudSave.SaveAsync("x", 1));
            Assert.IsNotType<SaveConflictException>(limit);

            t.Enqueue(204).Enqueue(404);
            Assert.True(await c.CloudSave.DeleteAsync("main", expectedVersion: 1));
            Assert.EndsWith("/v1/saves/main?expected_version=1", t.Requests[t.Requests.Count - 1].Url);
            Assert.False(await c.CloudSave.DeleteAsync("main"));
        }

        [Fact]
        public void Constructor_ValidatesOptions()
        {
            Assert.Throws<ArgumentException>(() => new LiveOpsClient(new LiveOpsOptions { ApiKey = "k", Transport = new FakeTransport() }));
            Assert.Throws<ArgumentException>(() => new LiveOpsClient(new LiveOpsOptions { BaseUrl = "x", Transport = new FakeTransport() }));
            Assert.Throws<ArgumentException>(() => new LiveOpsClient(new LiveOpsOptions { BaseUrl = "x", ApiKey = "k" }));
        }
    }
}

namespace LiveOps.Tests
{
    public class ProfileTests
    {
        [Fact]
        public async Task ProviderLogin_Profile_Link()
        {
            var t = new FakeTransport();
            var c = new LiveOpsClient(new LiveOpsOptions { BaseUrl = "https://api.test", ApiKey = "k", Transport = t });
            t.EnqueueLogin("tg", "p9");
            var r = await c.LoginWithProviderAsync("google", "id-token");
            Assert.Equal("p9", c.PlayerId);
            Assert.Equal("https://api.test/v1/auth/google", t.Requests[0].Url);
            Assert.Equal("{\"token\":\"id-token\"}", t.Requests[0].Body);

            const string profile = "{\"id\":\"p9\",\"display_name\":\"Neo\",\"created_at\":\"2026-01-01T00:00:00Z\",\"last_seen_at\":\"2026-01-01T00:00:00Z\",\"identities\":[{\"provider\":\"google\",\"email\":\"a@b.c\",\"created_at\":\"2026-01-01T00:00:00Z\"}]}";
            t.Enqueue(200, profile);
            var p = await c.SetDisplayNameAsync("Neo");
            Assert.Equal("PATCH", t.Requests[1].Method);
            Assert.Equal("Bearer tg", t.Requests[1].Headers["Authorization"]);
            Assert.Equal("Neo", p.DisplayName);
            Assert.Equal("google", p.Identities[0].Provider);

            t.Enqueue(409, "{\"detail\":{\"message\":\"account already linked to another player\",\"code\":\"identity_in_use\"}}");
            var ex = await Assert.ThrowsAsync<LiveOpsException>(() => c.LinkIdentityAsync("apple", "x"));
            Assert.Equal(409, ex.Status);
            Assert.EndsWith("/v1/players/me/identities/apple", t.Requests[2].Url);

            t.Enqueue(200, profile);
            await c.UnlinkIdentityAsync("google");
            Assert.Equal("DELETE", t.Requests[3].Method);
        }
    }
}
