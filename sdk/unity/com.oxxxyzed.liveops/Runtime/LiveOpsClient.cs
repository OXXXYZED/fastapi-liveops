using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace LiveOps
{
    /// <summary>
    /// Entry point of the SDK. Create one per environment and keep it for the app lifetime.
    /// All methods are async and continue on the caller's SynchronizationContext, so in Unity
    /// they resume on the main thread.
    /// </summary>
    public sealed class LiveOpsClient
    {
        private const int TokenRefreshMarginSeconds = 60;

        private readonly LiveOpsOptions _o;
        private readonly string _baseUrl;
        private readonly object _loginLock = new object();
        private Task<LoginResult> _loginInFlight;

        public string PlayerId => _o.Store.Get(K("player_id"));

        /// <summary>Last known config (fetched, or loaded from offline cache). Never null.</summary>
        public RemoteConfig Config { get; private set; } = RemoteConfig.Empty;

        /// <summary>Raised when <see cref="Config"/> changes after a fetch.</summary>
        public event Action<RemoteConfig> ConfigUpdated;

        public LeaderboardsApi Leaderboards { get; }
        public CloudSaveApi CloudSave { get; }

        public LiveOpsClient(LiveOpsOptions options)
        {
            if (options == null) throw new ArgumentNullException(nameof(options));
            if (string.IsNullOrEmpty(options.BaseUrl)) throw new ArgumentException("BaseUrl is required");
            if (string.IsNullOrEmpty(options.ApiKey)) throw new ArgumentException("ApiKey is required");
            if (options.Transport == null) throw new ArgumentException("Transport is required");
            _o = options;
            _o.Store = _o.Store ?? new MemoryStore();
            _baseUrl = options.BaseUrl.TrimEnd('/');

            var cached = _o.Store.Get(K("config"));
            if (cached != null)
            {
                try { Config = JsonConvert.DeserializeObject<RemoteConfig>(cached) ?? RemoteConfig.Empty; }
                catch (JsonException) { _o.Store.Delete(K("config")); }
            }

            Leaderboards = new LeaderboardsApi(this);
            CloudSave = new CloudSaveApi(this);
        }

        private string K(string name) => _o.StorePrefix + name;

        public string DeviceId
        {
            get
            {
                if (!string.IsNullOrEmpty(_o.DeviceId)) return _o.DeviceId;
                var id = _o.Store.Get(K("install_id"));
                if (id == null)
                {
                    id = Guid.NewGuid().ToString("N");
                    _o.Store.Set(K("install_id"), id);
                }
                return id;
            }
        }

        // ---------------- auth ----------------

        /// <summary>Logs in (or resumes the player bound to <see cref="DeviceId"/>).</summary>
        public Task<LoginResult> LoginAsync(CancellationToken ct = default)
        {
            lock (_loginLock)
            {
                if (_loginInFlight == null || _loginInFlight.IsCompleted)
                    _loginInFlight = DoLoginAsync(ct);
                return _loginInFlight;
            }
        }

        private Task<LoginResult> DoLoginAsync(CancellationToken ct) =>
            ApiKeyLogin("/v1/auth/anonymous", new Dictionary<string, string> { ["device_id"] = DeviceId }, ct);

        /// <summary>
        /// Signs in with a Google/Apple ID token or a Steam session ticket. Resumes the player that
        /// has this account linked, or creates a new one. Subsequent calls use this session.
        /// </summary>
        public Task<LoginResult> LoginWithProviderAsync(string provider, string token, CancellationToken ct = default) =>
            ApiKeyLogin("/v1/auth/" + Escape(provider), new Dictionary<string, string> { ["token"] = token }, ct);

        private async Task<LoginResult> ApiKeyLogin(string path, Dictionary<string, string> body, CancellationToken ct)
        {
            var req = NewRequest("POST", path);
            req.Headers["X-Api-Key"] = _o.ApiKey;
            req.Body = JsonConvert.SerializeObject(body);
            var res = await Send(req, ct);
            EnsureSuccess(res);
            var login = JsonConvert.DeserializeObject<LoginResult>(res.Body);
            var exp = _o.UtcNow().AddSeconds(login.ExpiresIn);
            _o.Store.Set(K("token"), login.AccessToken);
            _o.Store.Set(K("token_exp"), exp.ToString("o", CultureInfo.InvariantCulture));
            _o.Store.Set(K("player_id"), login.PlayerId);
            return login;
        }

        public bool HasValidToken
        {
            get
            {
                var token = _o.Store.Get(K("token"));
                var exp = _o.Store.Get(K("token_exp"));
                if (token == null || exp == null) return false;
                if (!DateTime.TryParse(exp, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var e))
                    return false;
                return _o.UtcNow().AddSeconds(TokenRefreshMarginSeconds) < e;
            }
        }

        public void Logout()
        {
            _o.Store.Delete(K("token"));
            _o.Store.Delete(K("token_exp"));
            _o.Store.Delete(K("player_id"));
        }

        // ---------------- profile ----------------

        public Task<PlayerProfile> GetProfileAsync(CancellationToken ct = default) =>
            PlayerJson<PlayerProfile>("GET", "/v1/players/me", null, ct);

        /// <summary>3–24 letters, digits, space, . _ -; null clears it.</summary>
        public Task<PlayerProfile> SetDisplayNameAsync(string displayName, CancellationToken ct = default) =>
            PlayerJson<PlayerProfile>("PATCH", "/v1/players/me",
                new Dictionary<string, string> { ["display_name"] = displayName }, ct);

        /// <summary>
        /// Links a Google/Apple/Steam account to the current player so progress follows it to other
        /// devices. Throws <see cref="LiveOpsException"/> 409 if the account belongs to another player.
        /// </summary>
        public Task<PlayerProfile> LinkIdentityAsync(string provider, string token, CancellationToken ct = default) =>
            PlayerJson<PlayerProfile>("POST", "/v1/players/me/identities/" + Escape(provider),
                new Dictionary<string, string> { ["token"] = token }, ct);

        public Task<PlayerProfile> UnlinkIdentityAsync(string provider, CancellationToken ct = default) =>
            PlayerJson<PlayerProfile>("DELETE", "/v1/players/me/identities/" + Escape(provider), null, ct);

        // ---------------- analytics ----------------

        /// <summary>
        /// Sends analytics events, forwarded server-side to webhooks subscribed to "player.event".
        /// Up to 50 per call; names are [a-zA-Z0-9_.:-]{1,64}.
        /// </summary>
        public Task TrackAsync(IEnumerable<AnalyticsEvent> events, CancellationToken ct = default)
        {
            var list = events.Select(e => new Dictionary<string, object>
            {
                ["name"] = e.Name,
                ["properties"] = e.Properties ?? new Dictionary<string, object>(),
                ["ts"] = (e.Timestamp ?? _o.UtcNow()).ToString("o", CultureInfo.InvariantCulture),
            }).ToList();
            return PlayerJson<object>("POST", "/v1/events", new Dictionary<string, object> { ["events"] = list }, ct);
        }

        public Task TrackAsync(string name, Dictionary<string, object> properties = null, CancellationToken ct = default) =>
            TrackAsync(new[] { new AnalyticsEvent { Name = name, Properties = properties } }, ct);

        // ---------------- remote config ----------------

        /// <summary>
        /// Fetches config and evaluated flags. Uses ETag caching. When the server is unreachable,
        /// keeps the cached config and returns it instead of throwing.
        /// </summary>
        public async Task<RemoteConfig> FetchConfigAsync(CancellationToken ct = default)
        {
            var query = string.Join("&", _o.Attributes
                .OrderBy(a => a.Key, StringComparer.Ordinal)
                .Select(a => Uri.EscapeDataString(a.Key) + "=" + Uri.EscapeDataString(a.Value ?? "")));
            var path = "/v1/config" + (query.Length > 0 ? "?" + query : "");
            var etagKey = K("config_etag");
            // The ETag is only valid together with the attributes it was computed for.
            var attrsKey = K("config_attrs");
            var etag = _o.Store.Get(attrsKey) == query ? _o.Store.Get(etagKey) : null;

            HttpResponse res;
            try
            {
                res = await PlayerRequest("GET", path, null, ct, etag);
            }
            catch (NetworkException)
            {
                return Config;
            }

            if (res.Status == 304) return Config;
            EnsureSuccess(res);
            var cfg = JsonConvert.DeserializeObject<RemoteConfig>(res.Body) ?? RemoteConfig.Empty;
            _o.Store.Set(K("config"), res.Body);
            _o.Store.Set(attrsKey, query);
            if (res.Headers.TryGetValue("etag", out var newEtag)) _o.Store.Set(etagKey, newEtag);
            else _o.Store.Delete(etagKey);
            Config = cfg;
            ConfigUpdated?.Invoke(cfg);
            return cfg;
        }

        // ---------------- plumbing ----------------

        internal async Task<T> PlayerJson<T>(string method, string path, object body, CancellationToken ct)
        {
            var res = await PlayerRequest(method, path, body == null ? null : JsonConvert.SerializeObject(body), ct);
            EnsureSuccess(res);
            return res.Status == 204 || string.IsNullOrEmpty(res.Body) ? default : JsonConvert.DeserializeObject<T>(res.Body);
        }

        /// <summary>Sends an authenticated request, logging in first and once more on 401.</summary>
        internal async Task<HttpResponse> PlayerRequest(
            string method, string path, string body, CancellationToken ct, string ifNoneMatch = null)
        {
            if (!HasValidToken) await LoginAsync(ct);
            for (var attempt = 0; ; attempt++)
            {
                var req = NewRequest(method, path);
                req.Headers["Authorization"] = "Bearer " + _o.Store.Get(K("token"));
                if (ifNoneMatch != null) req.Headers["If-None-Match"] = ifNoneMatch;
                req.Body = body;
                var res = await Send(req, ct);
                if (res.Status == 401 && attempt == 0)
                {
                    await LoginAsync(ct);
                    continue;
                }
                return res;
            }
        }

        private HttpRequest NewRequest(string method, string path)
        {
            var req = new HttpRequest { Method = method, Url = _baseUrl + path };
            req.Headers["Accept"] = "application/json";
            return req;
        }

        private async Task<HttpResponse> Send(HttpRequest req, CancellationToken ct)
        {
            var res = await _o.Transport.SendAsync(req, ct);
            if (res.NetworkError) throw new NetworkException();
            return res;
        }

        internal static void EnsureSuccess(HttpResponse res)
        {
            if (res.Status >= 200 && res.Status < 300) return;
            if (res.Status == 429)
            {
                var seconds = res.Headers.TryGetValue("retry-after", out var ra) && int.TryParse(ra, out var s) ? s : 1;
                throw new RateLimitedException(TimeSpan.FromSeconds(seconds), res.Body);
            }
            throw new LiveOpsException(res.Status, $"HTTP {res.Status}: {DetailOf(res.Body)}", res.Body);
        }

        internal static string DetailOf(string body)
        {
            try
            {
                var detail = JObject.Parse(body)["detail"];
                if (detail == null) return body;
                return detail.Type == JTokenType.String ? detail.ToString() : detail.ToString(Formatting.None);
            }
            catch (JsonException)
            {
                return body;
            }
        }

        internal static string Escape(string segment) => Uri.EscapeDataString(segment);
    }
}
