# Verified guide mappings

Reviewed 2026-11-10. An XMLTV guide having programmes is not proof that a stream's ID matches it. Aliases are explicit, not inferred from similar names; regional/time-shift identities stay separate.

| Canonical channel | XMLTV ID | Evidence |
| --- | --- | --- |
| `IranInternational.uk` (quality variants only) | `iranintl.iitv` | `SandObserver/live-tv-guide` README identifies the single-channel feed and ID; `output/iranintl.xml` at repository commit `4501066e7c4c54115835736136edaee58e6d8eb2` contained 292 programmes and real current-window coverage on 2026-11-10. Existing production stream ID is `IranInternational.uk@SD`. |

Provider: <https://github.com/SandObserver/live-tv-guide>. This is an **unofficial**, hourly scrape of the broadcaster's published schedule, not an Iranian national multi-channel guide. It replaces the confirmed 404 `epg.pw/xmltv/epg_IR.xml`, not a promise of full Iranian EPG coverage. Future builds still validate freshness and report gaps.

The US feed is processed with disk-backed compressed spooling (1 MiB RAM threshold, 64 MiB compressed cap) and incremental XML parsing (512 MiB expanded cap, 50,000 IDs / 2,000,000 programme observations / 250,000 disjoint windows). This deliberately does **not** load a 512 MiB XML document into memory or remove size/DTD/entity checks. Invalid/oversized refreshes cannot replace a valid cache. `xmltv-v3` separates the new summary schema/cache from older parsers.

## Reviewed national German mappings

The full isolated audit [42830171372](https://github.com/4kings6114/IPTV/actions/runs/42830171372), check-run `129877826428`, retrieved the actual DE XMLTV declarations on 2026-11-10 and emitted bounded identity evidence:

| Canonical channel | Provider ID in `https://epg.pw/xmltv/epg_DE.xml` | Declared display name |
| --- | --- | --- |
| `DasErste.de` | `76674` | `Das Erste` |
| `ZDF.de` | `76627` | `ZDF` |

Reviewed against the official iptv-org database's `data/channels.csv` (latest relevant commit `c838bc5b8b884d2f77f701d1215db26467104cb7`, 2026-11-10): exact national names/country DE and broadcaster websites `daserste.de` / `zdf.de`. Only unqualified/quality variants get these aliases; e.g. a future regional or time-shift variant will **not** inherit one. Every subsequent build must still find the declared ID and real current programmes in the guide; an alias never bypasses validation.

The same audit found provider `465021` named `CNN International` in the US guide, and DE `76650` / `76771` named `BBC News` / `BBC World News`. These are **not adopted**: existing CNN feeds have explicit Europe/Latin America/North America/South Asia variants, and the BBC regional-feed context is insufficient. A name-only cross-region guess would be worse than explicitly missing EPG.

Full-audit evidence at this initial phase-3 checkpoint: 37/37 fresh sources, 7,930 distinct URL/header requests checked, 5,179 structurally valid responses, 4,589 selected channels (330 temporarily retained unverified under the existing grace policy), +433 / −127 semantic identities, 213 URL/header changes. All production count/drop/source guards passed; the real production playlist and health/playback history were unchanged. This was a dry-run, not publication authorization and not all-channel decoding. EPG guide-level coverage: US 5,541/5,546, GB 752/755, DE 249/250, single-channel Iran International 1/1; selected-stream matched coverage at this checkpoint was only 1/4,589, before the two reviewed DE aliases were added.
