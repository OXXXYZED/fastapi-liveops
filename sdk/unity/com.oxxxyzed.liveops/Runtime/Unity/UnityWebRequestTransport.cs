#if UNITY_5_3_OR_NEWER
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine.Networking;

namespace LiveOps.Unity
{
    /// <summary>UnityWebRequest transport (works on all platforms incl. WebGL). Call from the main thread.</summary>
    public sealed class UnityWebRequestTransport : ITransport
    {
        private readonly int _timeoutSeconds;

        public UnityWebRequestTransport(int timeoutSeconds = 15) => _timeoutSeconds = timeoutSeconds;

        public async Task<HttpResponse> SendAsync(HttpRequest request, CancellationToken ct)
        {
            using (var uwr = new UnityWebRequest(request.Url, request.Method))
            {
                uwr.downloadHandler = new DownloadHandlerBuffer();
                if (request.Body != null)
                {
                    uwr.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(request.Body));
                    uwr.SetRequestHeader("Content-Type", "application/json");
                }
                foreach (var h in request.Headers) uwr.SetRequestHeader(h.Key, h.Value);
                uwr.timeout = _timeoutSeconds;

                var tcs = new TaskCompletionSource<bool>();
                using (ct.Register(() => { uwr.Abort(); tcs.TrySetCanceled(); }))
                {
                    uwr.SendWebRequest().completed += _ => tcs.TrySetResult(true);
                    await tcs.Task;
                }

                if (uwr.result == UnityWebRequest.Result.ConnectionError)
                    return new HttpResponse { NetworkError = true };

                var res = new HttpResponse { Status = (int)uwr.responseCode, Body = uwr.downloadHandler.text ?? "" };
                var headers = uwr.GetResponseHeaders();
                if (headers != null)
                    foreach (var h in headers) res.Headers[h.Key.ToLowerInvariant()] = h.Value;
                return res;
            }
        }
    }
}
#endif
