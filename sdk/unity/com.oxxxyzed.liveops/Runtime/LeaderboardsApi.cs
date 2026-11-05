using System.Threading;
using System.Threading.Tasks;

namespace LiveOps
{
    public sealed class LeaderboardsApi
    {
        private readonly LiveOpsClient _c;
        internal LeaderboardsApi(LiveOpsClient client) => _c = client;

        public Task<ScoreResult> SubmitScoreAsync(string board, long score, CancellationToken ct = default) =>
            _c.PlayerJson<ScoreResult>("POST", $"/v1/leaderboards/{LiveOpsClient.Escape(board)}/scores",
                new { score }, ct);

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
