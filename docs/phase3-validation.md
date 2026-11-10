# Phase 3 validation — 2026-11-10 UTC

This is development evidence, **not a production publication or an all-channel playback guarantee**. The branch is `arena/da8577cf-iptv`; PR: <https://github.com/4kings6114/IPTV/pull/3>. No production publisher was dispatched, no Telegram message sent, and no merge performed during this work.

## Full isolated audit

Run: <https://github.com/4kings6114/IPTV/actions/runs/42834844207>

- Code checkpoint: `6fc5a4dd03b4c4db66e4a123cce80ccc783898cf`.
- All four Python matrix versions, DOM QA, bounded live diagnostics **and** the full-audit job succeeded.
- Full-audit check-run: `129894203867`; analysis completed; real source/minimum/drop guards passed; `publication_authorized: false`.
- 37/37 sources fresh; **7,930 distinct eligible URL/header requests** checked, none using positive health TTL results. HLS child/redirect requests are additional internal GETs, not additional distinct candidate identities in this counter.
- Structural results: healthy 5,152; HTTP error 672; invalid 366; network error 528; restricted 622; timeout 590. HTTP 403 is restricted, not declared permanently dead.
- Selected: **4,576**, including **327 retained-unverified** under the existing failure-grace policy. Do not describe all 4,576 as verified playing streams.
- Baseline: 4,445 tracked M3U entries / 4,283 distinct stable identities. Semantic additions 421, removals 128, URL/header changes 220. `4,283 + 421 − 128 = 4,576`; raw-entry and semantic-identity counts differ because the baseline has alternatives/duplicates.
- Full build duration: 1,032.4 seconds. Python HTTP response bytes received: 89,207,271 (about 85.1 MiB); conservatively charged: 89,338,343 including interrupted-read reservations. Limits: 6,000 seconds / 1 GiB. TCP/TLS overhead is not included; decoding is disabled in the full audit and tested separately below.
- Production files and health/playback history preserved. Source/resource refresh caches are scratch-only and are allowed to refresh; they are not published health history.

This is a cold, isolated audit and not a prediction that a later warm-cache production run will select exactly the same streams. Provider responses and geographic restrictions vary. The previous earlier checkpoint [42830171372](https://github.com/4kings6114/IPTV/actions/runs/42830171372) selected 4,589 with 330 temporarily unverified; this variation is real, not an attempt to force a fixed green count.

## EPG results

Guide-level actual programme-window validation in the full audit:

| Guide | Declared IDs | Current programme coverage |
| --- | ---: | ---: |
| Iran International, unofficial hourly feed | 1 | 1 |
| epg.pw US | 5,546 | 5,541 |
| epg.pw GB | 755 | 752 |
| epg.pw DE | 250 | 249 |

The US guide succeeds with bounded streaming/spooling instead of failing the old expanded-64-MiB cap. This does **not** mean canonical stream IDs match all guide IDs.

**Matched selected-stream coverage is only 3/4,576**, with 4,573 explicitly uncovered. Three verified national/quality-only aliases are currently configured. Actual live evidence for their provider IDs:

- `iranintl.iitv`: covered, 292 programmes, Iran International names.
- `76674`: covered, 121 programmes, `Das Erste`.
- `76627`: covered, 102 programmes, `ZDF`.

See [EPG evidence and deliberate regional non-mappings](epg-evidence.md). No broad Iranian coverage, automatic fuzzy matching or comprehensive US coverage is claimed.

## Final implementation-head live diagnostics

Run: <https://github.com/4kings6114/IPTV/actions/runs/42836659067>

- Implementation head: `7dbb95dc917ab8208d1e938f62d1c1e949540f86`; subsequent documentation-only changes do not modify this tested implementation.
- Check-run: `129900566903`. Entire run succeeded, including Python 3.10/3.12/3.13/3.14 and the DOM job. Push and PR checks at this implementation head also succeeded.
- 37 fresh sources, 6,264 eligible source identities; 12 sampled requests checked, 9 selected structurally valid channels; second build reused 9 positive health observations without pretending they were fresh.
- The three mapped national identities were deliberately included in the sample; matched EPG coverage 3/9. **This is a prioritized sample, not a statistical coverage estimate.**
- Cinecanal Pacific: actual audio/video decode, 3 seconds, h264/mp2, 1080p.
- Das Erste: actual audio/video decode, 3 seconds, h264/aac, 1080p. At the earlier checkpoint it timed out despite A/V inspection; the later success does not make it permanently reliable or validate every alternative.
- Dry-run preserved output files and health/playback history. A minimum-count guard was also deliberately simulated and verified to preserve output. It is not a real production failure.
- No production file changed, no Telegram and no Pages deployment.

Full downloadable artifacts are retained on GitHub for 14 days; aggregate notices remain directly inspectable through the GitHub API:

```sh
gh api repos/4kings6114/IPTV/check-runs/129894203867/annotations --paginate
gh api repos/4kings6114/IPTV/check-runs/129900566903/annotations --paginate
```

## Local and offline QA

- **202 unit/local-HTTP tests pass**, with one explicit optional-decoder skip because the sandbox has no supported system decoder in PATH. Ordinary tests do not contact public stream servers.
- Source-only branch-aware coverage: **93%**, 2,574 executable statements / 1,020 branches.
- Ruff checks/format, Python 3.10 parsing, workflow YAML, extracted Bash syntax and full action-SHA pin validation pass.
- jsdom behavior tests pass: actual search/filter/pagination/profile links; local favorites/profile/M3U export; custom resolution/bitrate/source preference preservation; import size/schema limits; escaped malicious names and template markers; generated CSP hash. jsdom/Node are test-only, never a dashboard runtime dependency.
- Offline Arena preview: 4,331 channels derived from tracked files, **zero network checks**, explicit offline/unverified banner, no production modifications. HTTP 200 with the preview-style host; no host restriction or browser-side localhost calls. This preview is not the live-audit dataset and is not Pages.

## Pages deployment prerequisite

The repository is public, but the integration's Pages creation request returned **403 `Resource not accessible by integration`**. No site was enabled or deployed. The owner must select **Settings → Pages → Build and deployment → Source: GitHub Actions**. The integration may instead be reviewed/reconnected with appropriate permissions; never share credentials in chat.

Once enabled, the main publisher stages allowlisted safe files with latest-run telemetry and deploys after successful publication; missing setup produces an explicit warning/skip. An explicitly requested experimental diagnostic preview may also require the branch to be allowed by the `github-pages` environment. That preview replaces the site with a clearly labelled limited sample, not an independently private URL. Expected URL **only after setup and a successful deployment**: `https://4kings6114.github.io/IPTV/`.

Merge, a production rebuild/publication, and any replacement of the main playlist remain separate reviewed actions.
