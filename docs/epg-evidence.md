# Verified guide mappings

Reviewed 2026-11-10. An XMLTV guide having programmes is not proof that a stream's ID matches it. Aliases are explicit, not inferred from similar names; regional/time-shift identities stay separate.

| Canonical channel | XMLTV ID | Evidence |
| --- | --- | --- |
| `IranInternational.uk` (quality variants only) | `iranintl.iitv` | `SandObserver/live-tv-guide` README identifies the single-channel feed and ID; `output/iranintl.xml` at repository commit `4501066e7c4c54115835736136edaee58e6d8eb2` contained 292 programmes and real current-window coverage on 2026-11-10. Existing production stream ID is `IranInternational.uk@SD`. |

Provider: <https://github.com/SandObserver/live-tv-guide>. This is an **unofficial**, hourly scrape of the broadcaster's published schedule, not an Iranian national multi-channel guide. It replaces the confirmed 404 `epg.pw/xmltv/epg_IR.xml`, not a promise of full Iranian EPG coverage. Future builds still validate freshness and report gaps.

The US feed is processed with disk-backed compressed spooling (1 MiB RAM threshold, 64 MiB compressed cap) and incremental XML parsing (512 MiB expanded cap, 50,000 IDs / 2,000,000 programme observations / 250,000 disjoint windows). This deliberately does **not** load a 512 MiB XML document into memory or remove size/DTD/entity checks. Invalid/oversized refreshes cannot replace a valid cache. `xmltv-v3` separates the new summary schema/cache from older parsers.
