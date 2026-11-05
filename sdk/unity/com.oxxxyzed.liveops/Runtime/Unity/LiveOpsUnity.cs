#if UNITY_5_3_OR_NEWER
using UnityEngine;

namespace LiveOps.Unity
{
    public static class LiveOpsUnity
    {
        /// <summary>
        /// Creates a client wired with UnityWebRequest + PlayerPrefs and default targeting
        /// attributes (platform, app_version, unity_version). Add game-specific ones via Attributes.
        /// </summary>
        public static LiveOpsClient Create(string baseUrl, string apiKey)
        {
            var options = new LiveOpsOptions
            {
                BaseUrl = baseUrl,
                ApiKey = apiKey,
                Transport = new UnityWebRequestTransport(),
                Store = new PlayerPrefsStore(),
            };
            options.Attributes["platform"] = PlatformName();
            options.Attributes["app_version"] = Application.version;
            options.Attributes["unity_version"] = Application.unityVersion;
            return new LiveOpsClient(options);
        }

        private static string PlatformName()
        {
            switch (Application.platform)
            {
                case RuntimePlatform.IPhonePlayer: return "ios";
                case RuntimePlatform.Android: return "android";
                case RuntimePlatform.WebGLPlayer: return "webgl";
                case RuntimePlatform.WindowsPlayer:
                case RuntimePlatform.WindowsEditor: return "windows";
                case RuntimePlatform.OSXPlayer:
                case RuntimePlatform.OSXEditor: return "macos";
                case RuntimePlatform.LinuxPlayer:
                case RuntimePlatform.LinuxEditor: return "linux";
                default: return Application.platform.ToString().ToLowerInvariant();
            }
        }
    }
}
#endif
