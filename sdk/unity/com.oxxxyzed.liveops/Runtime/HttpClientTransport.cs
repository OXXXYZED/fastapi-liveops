using System;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace LiveOps
{
    /// <summary>System.Net.Http transport for non-Unity hosts (tools, dedicated servers, tests).</summary>
    public sealed class HttpClientTransport : ITransport, IDisposable
    {
        private readonly HttpClient _http;

        public HttpClientTransport(TimeSpan? timeout = null)
        {
            _http = new HttpClient { Timeout = timeout ?? TimeSpan.FromSeconds(15) };
        }

        public async Task<HttpResponse> SendAsync(HttpRequest request, CancellationToken ct)
        {
            using (var msg = new HttpRequestMessage(new HttpMethod(request.Method), request.Url))
            {
                foreach (var h in request.Headers)
                    msg.Headers.TryAddWithoutValidation(h.Key, h.Value);
                if (request.Body != null)
                    msg.Content = new StringContent(request.Body, Encoding.UTF8, "application/json");
                try
                {
                    using (var res = await _http.SendAsync(msg, ct))
                    {
                        var result = new HttpResponse
                        {
                            Status = (int)res.StatusCode,
                            Body = await res.Content.ReadAsStringAsync(),
                        };
                        foreach (var h in res.Headers)
                            result.Headers[h.Key.ToLowerInvariant()] = string.Join(",", h.Value);
                        foreach (var h in res.Content.Headers)
                            result.Headers[h.Key.ToLowerInvariant()] = string.Join(",", h.Value);
                        return result;
                    }
                }
                catch (HttpRequestException)
                {
                    return new HttpResponse { NetworkError = true };
                }
                catch (TaskCanceledException) when (!ct.IsCancellationRequested)
                {
                    return new HttpResponse { NetworkError = true }; // timeout
                }
            }
        }

        public void Dispose() => _http.Dispose();
    }
}
