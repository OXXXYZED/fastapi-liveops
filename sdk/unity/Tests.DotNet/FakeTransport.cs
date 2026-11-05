using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using LiveOps;

namespace LiveOps.Tests
{
    internal sealed class FakeTransport : ITransport
    {
        public readonly List<HttpRequest> Requests = new List<HttpRequest>();
        private readonly Queue<Func<HttpRequest, HttpResponse>> _responses = new Queue<Func<HttpRequest, HttpResponse>>();

        public FakeTransport Enqueue(int status, string body = "", Dictionary<string, string> headers = null)
        {
            _responses.Enqueue(_ =>
            {
                var r = new HttpResponse { Status = status, Body = body };
                if (headers != null) foreach (var h in headers) r.Headers[h.Key] = h.Value;
                return r;
            });
            return this;
        }

        public FakeTransport EnqueueNetworkError()
        {
            _responses.Enqueue(_ => new HttpResponse { NetworkError = true });
            return this;
        }

        public FakeTransport EnqueueLogin(string token = "tok", string playerId = "p1", int expiresIn = 3600) =>
            Enqueue(200, $"{{\"player_id\":\"{playerId}\",\"access_token\":\"{token}\",\"expires_in\":{expiresIn},\"created\":true}}");

        public Task<HttpResponse> SendAsync(HttpRequest request, CancellationToken ct)
        {
            Requests.Add(request);
            if (_responses.Count == 0) throw new InvalidOperationException($"unexpected request {request.Method} {request.Url}");
            return Task.FromResult(_responses.Dequeue()(request));
        }
    }
}
