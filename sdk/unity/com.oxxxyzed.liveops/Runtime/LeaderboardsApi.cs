using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace LiveOps
{
    public sealed class LeaderboardsApi
    {
        private readonly LiveOpsClient _c;
        internal LeaderboardsApi(LiveOpsClient client) => _c = client;

        /// <param name="metadata">
        /// Optional context forwarded to the server-side validation hook (match id, duration, replay hash…).
        /// A rejected score throws <see cref="LiveOpsException"/> with status 422.
        /// </param>
        public Task<ScoreResult> SubmitScoreAsync(
            string board, long score, Dictionary<string, object> metadata = null, CancellationToken ct = default)
        {
            var body = new Dictionary<string, object> { ["score"] = score };
            if (metadata != null) body["metadata"] = metadata;
            return _c.PlayerJson<ScoreResult>("POST", $"/v1/leaderboards/{LiveOpsClient.Escape(board)}/scores", body, ct);
        }

        /// <param name="season">"current", "previous" or an explicit id like "2026-W45".</param>
        public Task<LeaderboardPage> GetTopAsync(
            string board, int limit = 10, int offset = 0, string season = "current", CancellationToken ct = default) =>
            _c.PlayerJson<LeaderboardPage>("GET",
                $"/v1/leaderboards/{LiveOpsClient.Escape(board)}?limit={limit}&offset={offset}&season={LiveOpsClient.Escape(season)}",
                null, ct);

        public Task<MyRank> GetAroundMeAsync(
            string board, int radius = 5, string season = "current", CancellationToken ct = default) =>
            _c.PlayerJson<MyRank>("GET",
                $"/v1/leaderboards/{LiveOpsClient.Escape(board)}/me?radius={radius}&season={LiveOpsClient.Escape(season)}",
                null, ct);
    }
}
