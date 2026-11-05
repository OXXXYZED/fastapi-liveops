using System;
using System.Collections.Generic;

namespace LiveOps
{
    public sealed class LiveOpsOptions
    {
        /// <summary>Server base URL, e.g. https://liveops.example.com</summary>
        public string BaseUrl;

        /// <summary>Client API key for the environment (lo_client_...).</summary>
        public string ApiKey;

        /// <summary>
        /// Stable device/install identifier used to resume the same player.
        /// If null, the SDK generates one and persists it in <see cref="Store"/>.
        /// </summary>
        public string DeviceId;

        public ITransport Transport;
        public IKeyValueStore Store;

        /// <summary>Targeting attributes sent with config requests (platform, app_version, ...).</summary>
        public Dictionary<string, string> Attributes = new Dictionary<string, string>();

        /// <summary>Prefix for keys written to <see cref="Store"/>; change to run several clients.</summary>
        public string StorePrefix = "liveops.";

        /// <summary>Clock, overridable for tests.</summary>
        public Func<DateTime> UtcNow = () => DateTime.UtcNow;
    }
}
