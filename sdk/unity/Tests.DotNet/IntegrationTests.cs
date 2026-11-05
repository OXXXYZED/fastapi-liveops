using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using LiveOps;
using Xunit;

namespace LiveOps.Tests
{
    /// <summary>
    /// Runs against a real server when LIVEOPS_TEST_URL and LIVEOPS_TEST_API_KEY are set
    /// (see .github/workflows/ci.yml). The environment must have a leaderboard "it-main".
    /// </summary>
    public class IntegrationTests
    {
        private static readonly string Url = Environment.GetEnvironmentVariable("LIVEOPS_TEST_URL");
        private static readonly string Key = Environment.GetEnvironmentVariable("LIVEOPS_TEST_API_KEY");

        private static LiveOpsClient NewClient(string deviceId = null) => new LiveOpsClient(new LiveOpsOptions
        {
            BaseUrl = Url, ApiKey = Key, DeviceId = deviceId, Transport = new HttpClientTransport(),
            Attributes = new Dictionary<string, string> { ["platform"] = "ios" },
        });

        [SkippableFact]
        public async Task EndToEnd()
        {
            Skip.If(string.IsNullOrEmpty(Url) || string.IsNullOrEmpty(Key), "integration env not set");

            var device = "it-" + Guid.NewGuid().ToString("N");
            var c = NewClient(device);
            var login = await c.LoginAsync();
            Assert.True(login.Created);
            Assert.False((await NewClient(device).LoginAsync()).Created);

            var cfg = await c.FetchConfigAsync();
            Assert.True(cfg.Version >= 0);
            Assert.Same(cfg, await c.FetchConfigAsync()); // 304 path

            var profile = await c.SetDisplayNameAsync("Unity Tester");
            Assert.Equal("Unity Tester", profile.DisplayName);
            Assert.Empty(profile.Identities);

            var score = await c.Leaderboards.SubmitScoreAsync("it-main", 42);
            Assert.Equal(42, score.Score);
            var me = await c.Leaderboards.GetAroundMeAsync("it-main");
            Assert.Equal(login.PlayerId, me.Me.PlayerId);
            Assert.Equal("Unity Tester", me.Me.DisplayName);

            await c.TrackAsync("it_event", new Dictionary<string, object> { ["n"] = 1 });

            var saved = await c.CloudSave.SaveAsync("main", new Dictionary<string, int> { ["gold"] = 5 }, 0);
            await Assert.ThrowsAsync<SaveConflictException>(() => c.CloudSave.SaveAsync("main", 1, 0));
            var loaded = await c.CloudSave.LoadAsync<Dictionary<string, int>>("main");
            Assert.Equal(5, loaded.Data["gold"]);
            Assert.Equal(saved.Version, loaded.Version);
            Assert.True(await c.CloudSave.DeleteAsync("main"));
        }
    }
}
