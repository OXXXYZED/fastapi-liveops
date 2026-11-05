# LiveOps SDK for Unity

Client for [fastapi-liveops](https://github.com/OXXXYZED/fastapi-liveops).
Unity 2021.3+, all platforms including WebGL.

## Install

Package Manager → **Add package from git URL**:

```
https://github.com/OXXXYZED/fastapi-liveops.git?path=sdk/unity/com.oxxxyzed.liveops
```

Depends on `com.unity.nuget.newtonsoft-json` (pulled automatically).

## Usage

```csharp
using LiveOps;
using LiveOps.Unity;
using UnityEngine;

public class Bootstrap : MonoBehaviour
{
    public static LiveOpsClient LiveOps;

    async void Start()
    {
        LiveOps = LiveOpsUnity.Create("https://liveops.example.com", "lo_client_...");
        LiveOps.Attributes["country"] = "US";                        // extra targeting attributes

        // Cached config is available immediately, even offline.
        ApplyConfig(LiveOps.Config);
        LiveOps.ConfigUpdated += ApplyConfig;
        await LiveOps.FetchConfigAsync();                            // never throws on network errors

        await LiveOps.Leaderboards.SubmitScoreAsync("main", 1200);
        var top = await LiveOps.Leaderboards.GetTopAsync("main", limit: 20);
        var me = await LiveOps.Leaderboards.GetAroundMeAsync("main", radius: 3);

        // Optional: let the player keep progress across devices.
        // await LiveOps.LinkIdentityAsync("google", googleIdToken);   // or LoginWithProviderAsync on a new device
        await LiveOps.SetDisplayNameAsync("Neo");

        var save = await LiveOps.CloudSave.LoadAsync<Progress>("main");
        try
        {
            await LiveOps.CloudSave.SaveAsync("main", new Progress { Level = 4 }, save?.Version ?? 0);
        }
        catch (SaveConflictException e)
        {
            // Another device wrote first: reload, merge, retry with e.CurrentVersion.
        }
    }

    void ApplyConfig(RemoteConfig cfg)
    {
        var lives = cfg.Get("lives", 3);
        var discount = cfg.Get("shop.discount", 0f);
        var newShop = cfg.IsEnabled("new_shop");
    }

    class Progress { public int Level; }
}
```

## Behaviour

- **Auth** — logs in lazily on first call using a persisted install id (or `LiveOpsOptions.DeviceId`),
  refreshes the token 60 s before expiry and re-logs in once on `401`. Concurrent calls share one login.
- **Threading** — methods continue on the caller's `SynchronizationContext`, i.e. the Unity main thread.
- **Errors** — `LiveOpsException` (`Status`, `ResponseBody`), `NetworkException`,
  `RateLimitedException` (`RetryAfter`), `SaveConflictException` (`CurrentVersion`).
- **Storage** — tokens, install id and the last config live in `PlayerPrefs` under `liveops.*`.
  Provide your own `IKeyValueStore` for encrypted storage.
- **Outside Unity** — construct `LiveOpsClient` with `HttpClientTransport` and any `IKeyValueStore`.

## Development

Core logic has no UnityEngine dependency and is tested with plain .NET:

```bash
dotnet test sdk/unity/Tests.DotNet
```

After adding files to the package run `python scripts/gen_unity_meta.py` (CI checks this).
