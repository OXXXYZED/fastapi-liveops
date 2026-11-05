#if UNITY_5_3_OR_NEWER
using UnityEngine;

namespace LiveOps.Unity
{
    public sealed class PlayerPrefsStore : IKeyValueStore
    {
        public string Get(string key) => PlayerPrefs.HasKey(key) ? PlayerPrefs.GetString(key) : null;

        public void Set(string key, string value)
        {
            PlayerPrefs.SetString(key, value);
            PlayerPrefs.Save();
        }

        public void Delete(string key) => PlayerPrefs.DeleteKey(key);
    }
}
#endif
