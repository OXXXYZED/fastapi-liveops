using System;

namespace LiveOps
{
    public class LiveOpsException : Exception
    {
        public int Status { get; }
        public string ResponseBody { get; }

        public LiveOpsException(int status, string message, string body = null) : base(message)
        {
            Status = status;
            ResponseBody = body;
        }
    }

    public sealed class NetworkException : LiveOpsException
    {
        public NetworkException() : base(0, "network error: server unreachable") { }
    }

    public sealed class RateLimitedException : LiveOpsException
    {
        public TimeSpan RetryAfter { get; }

        public RateLimitedException(TimeSpan retryAfter, string body)
            : base(429, $"rate limited, retry after {retryAfter.TotalSeconds:0}s", body)
        {
            RetryAfter = retryAfter;
        }
    }

    /// <summary>Cloud save write rejected because the slot changed on the server.</summary>
    public sealed class SaveConflictException : LiveOpsException
    {
        /// <summary>Version currently on the server; 0 if the slot doesn't exist.</summary>
        public int CurrentVersion { get; }

        public SaveConflictException(int currentVersion, string body)
            : base(409, $"save conflict, server version is {currentVersion}", body)
        {
            CurrentVersion = currentVersion;
        }
    }
}
