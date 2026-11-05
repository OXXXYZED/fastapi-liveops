using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace LiveOps
{
    public sealed class CloudSaveApi
    {
        private readonly LiveOpsClient _c;
        internal CloudSaveApi(LiveOpsClient client) => _c = client;

        private static string Path(string slot) => "/v1/saves/" + LiveOpsClient.Escape(slot);

        public Task<List<SaveInfo>> ListAsync(CancellationToken ct = default) =>
            _c.PlayerJson<List<SaveInfo>>("GET", "/v1/saves", null, ct);

        /// <summary>Loads a slot, or returns null if it doesn't exist.</summary>
        public async Task<SaveData<T>> LoadAsync<T>(string slot, CancellationToken ct = default)
        {
            var res = await _c.PlayerRequest("GET", Path(slot), null, ct);
            if (res.Status == 404) return null;
            LiveOpsClient.EnsureSuccess(res);
            return JsonConvert.DeserializeObject<SaveData<T>>(res.Body);
        }

        /// <summary>
        /// Writes a slot. <paramref name="expectedVersion"/>: 0 = create only, N = only if the server
        /// still has version N, null = overwrite. Throws <see cref="SaveConflictException"/> on mismatch.
        /// </summary>
        public async Task<SaveInfo> SaveAsync<T>(
            string slot, T data, int? expectedVersion = null, CancellationToken ct = default)
        {
            var body = new Dictionary<string, object> { ["data"] = data };
            if (expectedVersion.HasValue) body["expected_version"] = expectedVersion.Value;
            var res = await _c.PlayerRequest("PUT", Path(slot), JsonConvert.SerializeObject(body), ct);
            ThrowIfConflict(res);
            LiveOpsClient.EnsureSuccess(res);
            return JsonConvert.DeserializeObject<SaveInfo>(res.Body);
        }

        /// <returns>False if the slot did not exist.</returns>
        public async Task<bool> DeleteAsync(string slot, int? expectedVersion = null, CancellationToken ct = default)
        {
            var path = Path(slot) + (expectedVersion.HasValue ? "?expected_version=" + expectedVersion.Value : "");
            var res = await _c.PlayerRequest("DELETE", path, null, ct);
            if (res.Status == 404) return false;
            ThrowIfConflict(res);
            LiveOpsClient.EnsureSuccess(res);
            return true;
        }

        private static void ThrowIfConflict(HttpResponse res)
        {
            if (res.Status != 409) return;
            try
            {
                var current = JObject.Parse(res.Body)["detail"]?["current_version"];
                if (current != null) throw new SaveConflictException(current.Value<int>(), res.Body);
            }
            catch (JsonException) { }
            // 409 without current_version (e.g. slot limit) falls through to a generic error.
        }
    }
}
