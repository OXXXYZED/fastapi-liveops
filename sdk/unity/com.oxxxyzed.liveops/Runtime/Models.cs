using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace LiveOps
{
    public sealed class LoginResult
    {
        [JsonProperty("player_id")] public string PlayerId;
        [JsonProperty("access_token")] public string AccessToken;
        [JsonProperty("expires_in")] public int ExpiresIn;
        [JsonProperty("created")] public bool Created;
    }

    public sealed class RemoteConfig
    {
        [JsonProperty("version")] public int Version;
        [JsonProperty("values")] public JObject Values = new JObject();
        [JsonProperty("flags")] public Dictionary<string, bool> Flags = new Dictionary<string, bool>();
        /// <summary>Experiment key → assigned variant. Variant overrides are already merged into Values.</summary>
        [JsonProperty("experiments")] public Dictionary<string, string> Experiments = new Dictionary<string, string>();

        public static readonly RemoteConfig Empty = new RemoteConfig();

        public bool IsEnabled(string flag) => Flags.TryGetValue(flag, out var on) && on;

        /// <summary>Assigned variant, or null if the player isn't enrolled.</summary>
        public string GetVariant(string experiment) =>
            Experiments != null && Experiments.TryGetValue(experiment, out var v) ? v : null;

        /// <summary>
        /// Reads a value by key; dots address nested objects ("shop.discount").
        /// Returns <paramref name="fallback"/> if missing or not convertible.
        /// </summary>
        public T Get<T>(string key, T fallback = default)
        {
            JToken token = Values;
            foreach (var part in key.Split('.'))
            {
                if (!(token is JObject obj) || !obj.TryGetValue(part, out token))
                    return fallback;
            }
            try
            {
                return token.Type == JTokenType.Null ? fallback : token.ToObject<T>();
            }
            catch (Exception e) when (e is JsonException || e is ArgumentException || e is FormatException || e is InvalidCastException)
            {
                return fallback;
            }
        }
    }

    public sealed class LeaderboardEntry
    {
        [JsonProperty("rank")] public int Rank;
        [JsonProperty("player_id")] public string PlayerId;
        [JsonProperty("score")] public long Score;
        [JsonProperty("display_name")] public string DisplayName;
    }

    public sealed class AnalyticsEvent
    {
        public string Name;
        public Dictionary<string, object> Properties;
        /// <summary>Client time; defaults to now (UTC).</summary>
        public DateTime? Timestamp;
    }

    public sealed class LinkedIdentity
    {
        [JsonProperty("provider")] public string Provider;
        [JsonProperty("email")] public string Email;
        [JsonProperty("created_at")] public DateTime CreatedAt;
    }

    public sealed class PlayerProfile
    {
        [JsonProperty("id")] public string Id;
        [JsonProperty("display_name")] public string DisplayName;
        [JsonProperty("created_at")] public DateTime CreatedAt;
        [JsonProperty("last_seen_at")] public DateTime LastSeenAt;
        [JsonProperty("identities")] public List<LinkedIdentity> Identities = new List<LinkedIdentity>();
    }

    public sealed class LeaderboardPage
    {
        [JsonProperty("key")] public string Key;
        [JsonProperty("season")] public string Season;
        [JsonProperty("total")] public int Total;
        [JsonProperty("entries")] public List<LeaderboardEntry> Entries = new List<LeaderboardEntry>();
    }

    public sealed class MyRank
    {
        [JsonProperty("key")] public string Key;
        [JsonProperty("season")] public string Season;
        [JsonProperty("total")] public int Total;
        [JsonProperty("me")] public LeaderboardEntry Me;
        [JsonProperty("around")] public List<LeaderboardEntry> Around = new List<LeaderboardEntry>();
    }

    public sealed class ScoreResult
    {
        [JsonProperty("season")] public string Season;
        [JsonProperty("score")] public long Score;
        [JsonProperty("rank")] public int Rank;
    }

    public class SaveInfo
    {
        [JsonProperty("slot")] public string Slot;
        [JsonProperty("version")] public int Version;
        [JsonProperty("size_bytes")] public int SizeBytes;
        [JsonProperty("updated_at")] public DateTime UpdatedAt;
    }

    public sealed class SaveData<T> : SaveInfo
    {
        [JsonProperty("data")] public T Data;
    }
}
