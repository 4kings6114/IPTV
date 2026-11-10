# IPTV همراه — Android phone trial

An isolated Kotlin/Compose/Media3 client for the existing catalog. **Experimental, not a production/Play release.** The user's 0.1.1 report identifies a Xiaomi phone running **Android API 31 (Android 12)**; an earlier description said Android 13. Test both API 31 and 33, and still retest the corrected APK on the physical phone. This does not change the Python builder, production playlists, Telegram notifier, Pages, or `main`.

## 0.1.2: confirmed TLS teardown crash

The supplied private local report identifies `NetworkOnMainThreadException`, wrapped by `ActivityThread.callActivityOnStop`, from `PlaybackActivity.releasePlayer -> ConnectionPool.evictAll -> ConscryptEngineSocket.close`. Closing an **idle HTTPS** socket can write TLS `close_notify`. Calling cancel/evict from `onStop` on main was therefore unsafe. It is not evidence of an unsupported codec, RAM shortage, or a bad stream.

The outgoing client is now detached on main and handed to an **application-owned IO cleanup coroutine**, not `lifecycleScope`, so destruction does not cancel scheduled cleanup. Cancellation, pool eviction, and executor shutdown all run on IO and independently attempt completion. Cleanup captures only the outgoing client, never the activity or a later retry/resume client's resources. Media3 release and PlayerView detachment remain on the main looper. StrictMode and normal TLS validation are not weakened.

The earlier 0.1.1 lifecycle-start correction and bounded, private, manually copied Java/Kotlin fatal report remain. No messages, URLs, credentials, thread names, or source-file paths are recorded; no telemetry is sent. Android's original uncaught handler is delegated to. Native faults, OS kills and some OOM events cannot be captured. A previous report remains until manually deleted; its version identifies which build actually crashed.

### Regression coverage

The **Android API31 and API33 stability** workflow runs on Android/workflow changes and manually. Each image must execute all five device tests without skips:

- At least 70 seconds of catalog/favorite UI activity and recreation.
- At least 75 seconds of generated loopback AVC/AAC playback, actual rotation, stop and a new engine on resume.
- Local-report read/copy/reopen/delete, with private-message omission.
- A **negative control** reproducing the old main-thread TLS pool-eviction exception on real Android Conscrypt; the test catches it inside its callback rather than crashing the process.
- Actual player `onStop`/resume/`onDestroy` with an idle TLS socket in the owned pool and main-thread StrictMode network death enabled. A test-only clone trusts a generated localhost certificate and shares the actual player's pool/dispatcher. This avoids adding a TLS bypass or injectable test hook to shipped code. Both outgoing cleanup completion and the next client's independence are checked.

The synthetic media belongs only to the instrumentation APK, never the application APK. Tests do not contact provider streams/logos and do not imply physical Poco/MIUI or live-provider compatibility. Consult the specific workflow run for results; defining a test is not evidence that it passed. Unit tests separately check IO socket closure, in-flight cancellation, repeated release, independent clients, and cleanup surviving owner-scope cancellation.

### Trial signing

The earlier installed debug key was not present in the signing cache at investigation time; do not promise an in-place update. CI now explicitly prepares/signs with the **same path it caches**, and packaging compares the APK signer to that key's public certificate. Private key material is never committed or uploaded in artifacts. The branch cache remains expendable, not production signing. `BUILD-INFO.json` reports compatibility with the previously delivered 0.1.0 and 0.1.1 certificate digests. Uninstalling a mismatched trial erases its local data; save needed source/favorite information privately first.

## Current scope

- Android 8+ (`minSdk 26`), `compileSdk/targetSdk 36`, phone layout, Persian RTL dark UI.
- Media3 1.11.1 device decoders with platform decoder fallback; HLS, DASH and supported progressive HTTP(S) media. No FFmpeg or LibVLC dependency, universal-codec promise, or DRM bypass.
- A 20-channel bundled **unverified** catalog snapshot for device trials. Selected from the root `playlist.m3u` at `eb813fff5ff1e0ed668d8b2ef47b410538809cdb`, 2026-11-10. Contains HLS, a DASH URL, an HTTP entry and a header-bearing entry, but these are URL/container examples, not verified codec samples. No live stream probe occurred when selecting them.
- Online presets (Persian, fa/en, low-bandwidth, all), manual refresh, custom M3U URL, Storage Access Framework file import. 8 MiB decompressed and 20,000-channel limits. An HLS stream manifest is not a channel-list M3U. Only HTTP(S), no URL user-info or local-file protocols.
- Categories, Persian/Arabic-normalized search, logos, device-local favorites and a bounded 40-entry playback history using Room. DataStore preferences. Favorites are independent of project-published `favorites.m3u`.
- Fullscreen player, subtitles/audio controls supplied by Media3, fit/crop and rotation. Playback releases the engine on `onStop`; no background playback, notifications, PiP, recordings or EPG yet.
- Classified, URL-free user diagnostics and manual retry. Media3's loader retries are bounded; no engine switching loops.

The `profile-low-bandwidth.m3u` preset selects supplied renditions. It does not transcode, create quality variants, or guarantee bandwidth. Source cache timestamps describe catalog imports, **not successful playback**.

## Build

Requirements: JDK 17; Android SDK platform 36 and build-tools 36.0.0; network access to official Gradle, Google Maven and Maven Central repositories. GitHub-hosted Ubuntu runners supply the SDK tools. No system Gradle install is necessary.

```sh
cd android
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
# Windows: .\gradlew.bat ...
```

APK: `app/build/outputs/apk/debug/app-debug.apk`.

Pinned toolchain: AGP 8.13.2, Gradle 8.13 (distribution SHA-256 checked), Kotlin/Compose compiler 2.2.21, KSP 2.2.21-2.0.4, Compose BOM 2025.10.01, Room 2.8.3, DataStore 1.1.7, OkHttp 4.12.0, Coil 2.7.0. Dependencies are intentionally version-pinned, not a claim that every dependency is the newest available. The official wrapper JAR was fetched from `gradle/gradle` tag `v8.13.0`, and its SHA-256 is checked against the Gradle release's `81a82aae…7ae45f` checksum before CI executes it.

The CI workflow `.github/workflows/android.yml` builds only test artifacts with read-only repository-content access (`checks: write` is used only for bounded verification diagnostics). It cannot publish playlists, deploy Pages, merge branches or create Releases. It uploads the actual APK without an outer ZIP, plus SHA-256, build metadata and [Persian installation guide](INSTALL-fa.md). Debug signing identity is cached in the branch's Actions cache, **not committed or uploaded in artifacts**; it is expendable, not a production signing identity. Cache loss can require uninstalling the previous trial. Use a separately controlled permanent key for any final distribution.

Application ID: `io.github.fourkings6114.iptv.trial` (debug); display name is provisional. Unsigned release configuration is deliberately separate.

## Security / privacy boundaries

- `INTERNET` plus the library-merged `ACCESS_NETWORK_STATE` normal permission; the unused Media3 `WAKE_LOCK` permission is explicitly removed because playback is foreground-only; file import uses a user-picked URI, not broad storage access. No account, advertising, analytics, location or Telegram integration.
- HTTP disabled in app policy by default, with persistent opt-in only after a warning. Manifest-level cleartext support is required for arbitrary IPTV hosts, but the shared OkHttp application and network interceptors enforce policy for playlists, media manifests, redirects, segments, keys and logos. Pure HTTP data sources prevent manifest access to local files.
- HTTPS uses normal system certificate/hostname verification; no trust-all manager or custom CA bypass. User-imported raw URLs with user-info are rejected.
- Stream-specific headers are sanitized and scoped to exact scheme/host/port. They are removed on cross-origin redirects/child requests. Some providers requiring credential sharing with another CDN therefore need a future explicit, narrowly scoped configuration, not indiscriminate header forwarding.
- Imports and refreshes are bounded and parsed before a Room transaction replaces source references. Failure preserves last-known-good data. Favorites and history survive removal from a refreshed source. Channel keys currently include URL and headers, so rotating tokens/URLs can produce a new favorite identity; persistent cross-refresh provider identity is future work.
- Private app storage, with backup/data transfer excluded; no separate database encryption. Logos/streams/providers necessarily see the phone's IP. Turn off logos to avoid those image requests.
- Copied diagnostics include version, Android API, device model, Media3 version, error code and selected MIME formats; never the URL or request headers. Media3 logging is disabled by the application.

## Verification and next steps

CI runs pure Kotlin parser/search tests, local-loopback network policy/TLS/header tests, Room transaction tests under Robolectric **API 33**, lint and APK signature/manifest/archive checks. Robolectric is not an emulator or a real Poco X3; no test result implies real video/audio decoder compatibility. Main-playlist ingestion tests are offline and adapt to the checked-in playlist rather than hard-coding historical channel counts.

The schema is exported for future non-destructive migrations. No destructive migration fallback is enabled. Check native dependencies/ELF page alignment and licensing again before any production release; FFmpeg/LibVLC, if later introduced based on concrete device errors, need their own native, ABI, 16 KiB and license validation.

Next: follow [INSTALL-fa.md](INSTALL-fa.md), collect a few redacted device errors, stabilize phone playback first, then add only the decoders/fallbacks actually required. There is no need for a video-hosting backend to start.

Bundled third-party notices and the Apache-2.0 text are available in Settings. Playlist metadata and public URLs are not proof of content rights. Only use content you are authorized to access.
