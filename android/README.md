# IPTV همراه — Android phone trial

An isolated Kotlin/Compose/Media3 client for the existing catalog. **Experimental, not a production/Play release.** The original 0.1.0 trial installed on the user's Poco X3 / Android 13 (API 33), but the user reports a crash after about one minute. This diagnostic build still requires physical-device retesting. This does not change the Python builder, production playlists, Telegram notifier, Pages, or `main`.

## 0.1.1 crash investigation

The user reports an Android/MIUI process crash after roughly one minute, not merely a Media3 `PlaybackException`. The precise cause is **not yet known without the phone's exception/native-crash summary**. Do not describe the screenshot alone as proof of a codec, VPN, memory or network fault.

This diagnostic patch adds a bounded, private, manually copied Java/Kotlin fatal-exception report (classes/frames and technical device/heap metadata only; no Throwable messages, thread names, source-file paths, URLs, headers or channel data). Android's original uncaught handler is always delegated to; the app never attempts to recover a damaged process. No telemetry is sent. Native crashes/OS kills and some OOM events cannot be captured this way. The report can be copied or deleted in the next run.

A separate foreground-resume bug is fixed: `Lifecycle.currentState` can still be `CREATED` inside `onStart`, so checking for `STARTED` there could suppress player recreation. The player now tracks actual start/stop callbacks. This is not, by itself, confirmation of the reported one-minute crash's cause.

The **Android API33 stability smoke** workflow (automatic on Android/workflow changes, also manually runnable) tests prolonged browsing and actual generated AVC/AAC video over loopback, including verified rotation, release in background, and a new engine on resume. It also tests reading, copying, reopening, and deleting a synthetic local fatal report. It does not access provider streams or logos, and the synthetic fixture belongs only to the instrumentation APK, never the application APK. An emulator result is not a Poco X3/MIUI result. See the workflow run for whether these tests actually completed; adding a test is not evidence that it passed.

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
