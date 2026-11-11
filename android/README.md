# IPTV همراه — Android phone trial

An isolated Kotlin/Compose/Media3 client for the existing catalog. **Experimental, not a production/Play release.** The user's 0.1.1 report identifies a Xiaomi phone running **Android API 31 (Android 12)**; an earlier description said Android 13. Test both API 31 and 33, and still retest the corrected APK on the physical phone. This does not change the Python builder, production playlists, Telegram notifier, Pages, or `main`.

## 0.1.2: confirmed TLS teardown crash

The supplied private local report identifies `NetworkOnMainThreadException`, wrapped by `ActivityThread.callActivityOnStop`, from `PlaybackActivity.releasePlayer -> ConnectionPool.evictAll -> ConscryptEngineSocket.close`. Closing an **idle HTTPS** socket can write TLS `close_notify`. Calling cancel/evict from `onStop` on main was therefore unsafe. It is not evidence of an unsupported codec, RAM shortage, or a bad stream.

The outgoing client is now detached on main and handed to an **application-owned IO cleanup coroutine**, not `lifecycleScope`, so destruction does not cancel scheduled cleanup. Cancellation, pool eviction, and executor shutdown all run on IO and independently attempt completion. Cleanup captures only the outgoing client, never the activity or a later retry/resume client's resources. Media3 release and PlayerView detachment remain on the main looper. StrictMode and normal TLS validation are not weakened.

The earlier 0.1.1 lifecycle-start correction and bounded, private, manually copied Java/Kotlin fatal report remain. No messages, URLs, credentials, thread names, or source-file paths are recorded; no telemetry is sent. Android's original uncaught handler is delegated to. Native faults, OS kills and some OOM events cannot be captured. A previous report remains until manually deleted; its version identifies which build actually crashed.

### Regression coverage

The **Android API31 and API33 stability** workflow runs on Android/workflow changes and manually. Each image retains the five playback/report cases below without skips, in addition to the new source-management and mandatory version-specific upgrade phases described under Verification:

- At least 70 seconds of catalog/favorite UI activity and recreation.
- At least 75 seconds of generated loopback AVC/AAC playback, actual rotation, stop and a new engine on resume.
- Local-report read/copy/reopen/delete, with private-message omission.
- A **negative control** reproducing the old main-thread TLS pool-eviction exception on real Android Conscrypt; the test catches it inside its callback rather than crashing the process.
- Actual player `onStop`/resume/`onDestroy` with an idle TLS socket in the owned pool and main-thread StrictMode network death enabled. A test-only clone trusts a generated localhost certificate and shares the actual player's pool/dispatcher. This avoids adding a TLS bypass or injectable test hook to shipped code. Both outgoing cleanup completion and the next client's independence are checked.

The synthetic media belongs only to the instrumentation APK, never the application APK. Tests do not contact provider streams/logos and do not imply physical Poco/MIUI or live-provider compatibility. Consult the specific workflow run for results; defining a test is not evidence that it passed. Unit tests separately check IO socket closure, in-flight cancellation, repeated release, independent clients, and cleanup surviving owner-scope cancellation.

### Trial signing

The 0.1.1 -> 0.1.2 transition previously lost its cached debug identity and was not an in-place update. That is historical, not the intended 0.1.3 upgrade path. CI explicitly prepares/signs with the **same path it caches**, compares the APK signer to that key's public certificate, and now refuses to package 0.1.3 unless it matches the delivered 0.1.2 signer. `BUILD-INFO.json` records `updates_delivered_0_1_2_trial` and older certificate compatibility. Install the verified 0.1.3 as an update to 0.1.2; do not uninstall or clear local data. Stop and review an installation error rather than erasing saved information. Private key material is never committed or uploaded in artifacts. The branch cache remains expendable, not production signing.

## Current scope

- Android 8+ (`minSdk 26`), `compileSdk/targetSdk 36`, phone layout, Persian RTL dark UI.
- Media3 1.11.1 device decoders with platform decoder fallback; HLS, DASH and supported progressive HTTP(S) media. No FFmpeg or LibVLC dependency, universal-codec promise, or DRM bypass.
- A 20-channel bundled **unverified** catalog snapshot for device trials. Selected from the root `playlist.m3u` at `eb813fff5ff1e0ed668d8b2ef47b410538809cdb`, 2026-11-10. Contains HLS, a DASH URL, an HTTP entry and a header-bearing entry, but these are URL/container examples, not verified codec samples. No live stream probe occurred when selecting them.
- Online presets (Persian, fa/en, low-bandwidth, all) and **multiple independent, named M3U URL/file catalogs**. Every successful add receives a new UUID, even for the same URL/file. Manage selection, rename, refresh and confirmed deletion separately; built-in presets cannot be renamed/deleted. 8 MiB decompressed and 20,000-channel limits per catalog. An HLS stream manifest is not a channel-list M3U. Only HTTP(S), no URL user-info or local-file protocols.
- File imports use the Storage Access Framework without retaining a URI or requesting broad storage permission. Refresh explicitly asks for the file again and replaces only the target catalog; cancelling changes nothing. Adding a file always creates another catalog. Source names are bounded to 80 characters, with controls/bidi overrides removed and Persian ZWNJ preserved.
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

The CI workflow `.github/workflows/android.yml` builds only test artifacts with read-only repository-content access (`checks: write` is used only for bounded verification diagnostics). It cannot publish playlists, deploy Pages, merge branches or create Releases. It uploads the actual APK without an outer ZIP, plus SHA-256, build metadata and [Persian installation guide](INSTALL-fa.md). Debug signing identity is cached in the branch's Actions cache, **not committed or uploaded in artifacts**; it is expendable, not a production signing identity. The 0.1.3 package gate requires the signer of the delivered 0.1.2 APK; cache loss fails the gate instead of silently delivering a data-losing, incompatible update. A separately controlled/backed-up permanent key is still required for final distribution; that work is not included here.

Application ID: `io.github.fourkings6114.iptv.trial` (debug); display name is provisional. Unsigned release configuration is deliberately separate.

## Security / privacy boundaries

- `INTERNET` plus the library-merged `ACCESS_NETWORK_STATE` normal permission; the unused Media3 `WAKE_LOCK` permission is explicitly removed because playback is foreground-only; file import uses a user-picked URI, not broad storage access. No account, advertising, analytics, location or Telegram integration.
- HTTP disabled in app policy by default, with persistent opt-in only after a warning. Manifest-level cleartext support is required for arbitrary IPTV hosts, but the shared OkHttp application and network interceptors enforce policy for playlists, media manifests, redirects, segments, keys and logos. Pure HTTP data sources prevent manifest access to local files.
- HTTPS uses normal system certificate/hostname verification; no trust-all manager or custom CA bypass. User-imported raw URLs with user-info are rejected.
- Stream-specific headers are sanitized and scoped to exact scheme/host/port. They are removed on cross-origin redirects/child requests. Some providers requiring credential sharing with another CDN therefore need a future explicit, narrowly scoped configuration, not indiscriminate header forwarding.
- Imports and refreshes are bounded and parsed before a Room transaction replaces source references. Failure/cancellation preserves last-known-good data. A repository mutex serializes refresh, rename and deletion so an in-flight refresh cannot resurrect a deleted catalog. Deletion is a Room transaction that removes only that catalog and unreferenced, unsaved channels; shared channels, favorites and history survive, including deletion of the last personal catalog. Deleting a selected source returns to the bundled offline sample. The chosen external file is never modified/deleted.
- Room remains **schema v1**, without a destructive migration. Existing `custom`/`file` source IDs, names, timestamps and DataStore selections are retained unchanged and remain manageable. A stale/missing personal selection recovers to the sample; it never invents a replacement source.
- Channel keys include URL, TVG ID and headers, so rotating tokens/URLs can produce a new favorite identity; persistent cross-refresh provider identity is future work. Identical channel identities share their metadata across catalogs, as in v1; deleting one catalog never removes a channel still referenced by another.
- Private app storage, with backup/data transfer excluded; no separate database encryption. Logos/streams/providers necessarily see the phone's IP. Turn off logos to avoid those image requests.
- Copied diagnostics include version, Android API, device model, Media3 version, error code and selected MIME formats; never the URL or request headers. Media3 logging is disabled by the application.

## Verification and next steps

CI runs pure Kotlin parser/search tests, local-loopback network policy/TLS/header tests, Room/repository tests under Robolectric **API 33**, lint, instrumentation compilation and APK signature/manifest/archive checks. Source-management regressions cover repeated URL/file adds, legacy IDs, targeted file replacement, empty/failed/cancelled refresh, name validation, protected presets, rename isolation, deletion rollback, shared/saved-channel retention and concurrent refresh/delete. DataStore tests verify selection compare-and-reset without erasing HTTP/logo settings. Robolectric is not an emulator or a real Poco X3; no test result implies real video/audio decoder compatibility. Main-playlist ingestion tests are offline and adapt to the checked-in playlist rather than hard-coding historical channel counts.

The API 31/33 emulator workflow also rebuilds the immutable delivered 0.1.2 **source** (`93b2152…`) with the cached signer, seeds legacy sources/favorites/history/preferences, performs a real `adb install -r` to 0.1.3, and verifies preservation in a fresh target process. It compares both generated Room v1 schemas and checks the signer against the delivered 0.1.2 public certificate. This is a source-rebuilt baseline, **not a claim of byte-identical rebuilding of the previously downloaded APK**; hashes and scope are recorded. Upgrade seed/verify tests run in separate mandatory version-specific phases, followed by eight ordinary runtime cases (including independent catalog UI and file-result/recreation tests). The system document-picker UI itself is not automated; file results are supplied programmatically. Existing prolonged synthetic playback and strict TLS teardown controls remain required. This is not physical-device/provider-stream certification.

The schema is exported for future non-destructive migrations. No destructive migration fallback is enabled. Check native dependencies/ELF page alignment and licensing again before any production release; FFmpeg/LibVLC, if later introduced based on concrete device errors, need their own native, ABI, 16 KiB and license validation.

Next: follow [INSTALL-fa.md](INSTALL-fa.md), verify the in-place 0.1.2 update and independent catalogs on the phone, then plan permanent backed-up signing before broader distribution. Add decoders/fallbacks only for concrete device errors. There is no need for a video-hosting backend to start.

Bundled third-party notices and the Apache-2.0 text are available in Settings. Playlist metadata and public URLs are not proof of content rights. Only use content you are authorized to access.
