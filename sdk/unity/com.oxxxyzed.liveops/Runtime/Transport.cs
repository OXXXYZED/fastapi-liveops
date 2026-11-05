using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace LiveOps
{
    public sealed class HttpRequest
    {
        public string Method = "GET";
        public string Url = "";
        public string Body;
        public readonly Dictionary<string, string> Headers = new Dictionary<string, string>();
    }

    public sealed class HttpResponse
    {
        public int Status;
        public string Body = "";
        /// <summary>Header names are lower-cased.</summary>
        public readonly Dictionary<string, string> Headers = new Dictionary<string, string>();

        /// <summary>True when the request never reached the server (DNS, offline, timeout).</summary>
        public bool NetworkError;
    }

    /// <summary>
    /// HTTP abstraction. Unity uses <see cref="Unity.UnityWebRequestTransport"/>; tests and
    /// non-Unity .NET hosts can use <see cref="HttpClientTransport"/> or a fake.
    /// </summary>
    public interface ITransport
    {
        Task<HttpResponse> SendAsync(HttpRequest request, CancellationToken ct);
    }

    /// <summary>Persistent key-value storage for tokens and offline config cache.</summary>
    public interface IKeyValueStore
    {
        string Get(string key);
        void Set(string key, string value);
        void Delete(string key);
    }

    public sealed class MemoryStore : IKeyValueStore
    {
        private readonly Dictionary<string, string> _data = new Dictionary<string, string>();
        public string Get(string key) => _data.TryGetValue(key, out var v) ? v : null;
        public void Set(string key, string value) => _data[key] = value;
        public void Delete(string key) => _data.Remove(key);
    }
}
