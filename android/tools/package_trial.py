"""Package only the verified test APK, never keys, tokens, or runtime state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import traceback
from ci_report import publish
from signing_support import EXPECTED_SIGNER_SHA256, certificate_digest
import xml.etree.ElementTree as ET
import zipfile

def report_failure(kind, error, trace):
    publish("Android APK package details", 1, "".join(traceback.format_exception(kind, error, trace)))
    sys.__excepthook__(kind, error, trace)


sys.excepthook = report_failure

root = Path(__file__).resolve().parents[1]
app = root / "app"
build = app / "build"
apk = build / "outputs/apk/debug/app-debug.apk"
metadata = json.loads((apk.parent / "output-metadata.json").read_text())
element = metadata["elements"][0]
assert metadata["applicationId"] == "io.github.fourkings6114.iptv.trial"
assert element["versionName"].endswith("-trial")
assert element["versionCode"] == 6
assert element["versionName"] == "0.1.5-trial"
badging = (build / "badging.txt").read_text()
assert "sdkVersion:'26'" in badging
assert "targetSdkVersion:'36'" in badging
permissions = set(re.findall(r"uses-permission: name='([^']+)'", badging))
# The only optional merged permission is AndroidX's signature-protected receiver permission.
assert all(name in {"android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE"}
           or name == metadata["applicationId"] + ".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"
           for name in permissions), permissions
assert "android.permission.INTERNET" in permissions
with zipfile.ZipFile(apk) as archive:
    assert archive.testzip() is None
    assert "AndroidManifest.xml" in archive.namelist()
    assert "classes.dex" in archive.namelist()
    assert "assets/stability.mp4" not in archive.namelist(), "Instrumentation fixture must not ship in app APK"
    assert len([line for line in archive.read("assets/trial.m3u").decode().splitlines()
                if line.startswith("#EXTINF:")]) == 20
    assert not any(name.endswith((".keystore", ".jks", ".env")) for name in archive.namelist())
    native_libraries = sorted(name for name in archive.namelist() if name.endswith(".so"))

counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
source_test_suites = {}
pip_test_suites = {}
epg_test_suites = {}
executed_cases = set()
for path in (build / "test-results/testDebugUnitTest").glob("TEST-*.xml"):
    suite = ET.parse(path).getroot()
    for name in counts:
        counts[name] += int(suite.get(name, "0"))
    executed_cases.update(case.get("name") for case in suite.findall("testcase"))
    if suite.get("name", "").rsplit(".", 1)[-1] in {"AppDatabaseTest", "PlaylistRepositoryTest", "SettingsStoreTest", "SourceRulesTest"}:
        source_test_suites[suite.get("name")] = int(suite.get("tests", "0"))
    if suite.get("name", "").rsplit(".", 1)[-1] in {"PipRulesTest", "SettingsStoreTest"}:
        pip_test_suites[suite.get("name")] = int(suite.get("tests", "0"))
    if suite.get("name", "").rsplit(".", 1)[-1] in {"EpgGuideTest", "XmlTvParserTest", "EpgCacheTest", "EpgNetworkTest", "EpgRepositoryTest"}:
        epg_test_suites[suite.get("name")] = int(suite.get("tests", "0"))
required_source_cases = {
    "repeatedUrlAddsAreIndependentAndDoNotOverwriteLegacyCustom",
    "repeatedFileAddsAreIndependentAndDoNotOverwriteLegacyFile",
    "targetedFileRefreshRetainsIdentityNameAndSavedChannels",
    "failedUrlRefreshPreservesLastGoodMetadataReferencesFavoritesAndHistory",
    "cancelledRefreshKeepsTheLastKnownGoodSource",
    "deletionPreservesOtherSourcesSharedChannelsFavoritesAndHistory",
    "deletingTheLastPersonalSourceDoesNotDeleteSavedChannels",
    "refreshAndDeleteAreSerializedSoACompletedDeleteCannotBeResurrected",
    "sourceDeletionRollsBackReferencesWhenTheTransactionFails",
    "forgettingOnlyTheSelectedSourcePreservesOtherSelectionsAndPreferences",
}
assert required_source_cases <= executed_cases, required_source_cases - executed_cases
required_pip_cases = {
    "manualEntryDoesNotRequireAutomaticHomeOptIn",
    "pausedVideoCanBeManuallyMinimizedButCannotAutoEnter",
    "unsupportedDeviceNeverEntersPip",
    "stoppedPlaybackCannotBeResurrectedByPip",
    "missingVideoOrAudioOnlyPlaybackNeverEntersPip",
    "failedPlaybackDoesNotAutomaticallyLeaveAnErrorWindow",
    "explicitBackOrFinishNeverAutoEntersPip",
    "normalVideoRatioIsPreservedWithinRoundingTolerance",
    "anamorphicPixelAspectRatioIsApplied",
    "ultraWideRatioIsClampedToThePlatformMaximum",
    "extremelyTallRatioRoundsInsideThePlatformMinimum",
    "invalidOrUnknownDimensionsUseASafeFallback",
    "invalidPixelRatiosCannotProduceAnInvalidPlatformRational",
    "extremeFinitePixelRatiosStayWithinBothPlatformBounds",
    "automaticPictureInPictureDefaultsOffAndPersistsIndependently",
}
assert required_pip_cases <= executed_cases, required_pip_cases - executed_cases
required_epg_cases = {
    "xmltvOffsetsAndMissingZoneDoNotDependOnThePhoneZone",
    "nowNextUsesStartInclusiveStopExclusiveAndSortedFutureStarts",
    "exactCaseSensitiveIdsDoNotGuessFromNamesOrPrefixes",
    "xmltvSupportsPlainGzipHttpDoubleGzipAndUtf16WithoutEncodingGuesses",
    "xmltvRejectsInternalDtdEntitiesAndUtf16XxeBeforeExpansion",
    "xmltvExternalDoctypeIsIgnoredAndNeverFetched",
    "compressedAndExpandedByteLimitsAreEnforcedIndependently",
    "giantTagsAttributesTextAndDeepNestingHitBoundedLimits",
    "cancelledAtomicCacheWritesPreserveTheLastKnownGoodGuide",
    "failedGuideRefreshPreservesGoodCacheCatalogFavoritesHistoryAndPreferences",
    "cancelledGuideBodyCannotReplaceTheLastKnownGoodCache",
    "lateGuideResponseCannotRestoreAnOldUrlAfterAnAToBToAConfigurationChange",
    "lateGuideResponseCannotResurrectADeletedCatalog",
    "catalogRefreshDuringGuideDownloadDiscardsTheOldCatalogResult",
    "rewrittenM3uGuideIdsPersistWithoutChangingFavoritesOrLeakingMediaHeaders",
    "sameSharedChannelCanUseIndependentXmltvIdsInTwoCatalogs",
    "guideRequestsUseNoStreamCredentialsAndDecodeHttpAndFileGzipExplicitly",
    "disabledHttpGuideNeverReachesTheServer",
    "guideHttpsValidationIsNotBypassedAndDowngradeRedirectsStillObeyHttpPolicy",
    "guideCancellationStillWorksAfterResponseHeadersWereReceived",
    "xmltvUrlsAreIndependentPerCatalogAndForgetDoesNotResetPipOrOtherUrls",
    "rewrittenXmltvIdDoesNotAlterLegacyKeyOrBecomeARequestHeader",
}
assert required_epg_cases <= executed_cases, required_epg_cases - executed_cases
assert counts["tests"] >= 129, counts
assert counts["failures"] == counts["errors"] == counts["skipped"] == 0, counts
lint = ET.parse(build / "reports/lint-results-debug.xml").getroot()
assert not any(issue.get("severity") in {"Fatal", "Error"} for issue in lint.findall("issue"))

output = build / "deliverables"
output.mkdir(parents=True, exist_ok=True)
name = f"IPTV-Hamrah-{element['versionName']}.apk"
destination = output / name
shutil.copyfile(apk, destination)
digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(output / (name + ".sha256")).write_text(f"{digest}  {name}\n")
info = {
    "application_id": metadata["applicationId"],
    "version_name": element["versionName"],
    "version_code": element["versionCode"],
    "min_sdk": 26,
    "target_sdk": 36,
    "commit": os.environ["BUILD_SHA"],
    "run_id": os.environ["BUILD_RUN_ID"],
    "apk": name,
    "bytes": destination.stat().st_size,
    "sha256": digest,
    "unit_tests": counts,
    "source_management_test_suites": source_test_suites,
    "picture_in_picture_test_suites": pip_test_suites,
    "epg_test_suites": epg_test_suites,
    "permissions": sorted(permissions),
    "native_libraries": native_libraries,
    "signing": "Retained DEBUG/TRIAL identity; protected storage migration is separate from production key generation",
    "signing_source": os.environ["IPTV_SIGNING_SOURCE"],
    "physical_device_test": "0.1.5 EPG NOT YET TESTED on a physical device; user confirmed 0.1.4 manual PiP and reported 0.1.3 stable on their phone",
    "picture_in_picture": "Native Activity PiP (API26+ if supported); manual entry after decoded video; optional Home auto-entry defaults OFF; system pause/play/expand/dismiss; no overlay permission or background service",
    "epg": {
        "scope": "Explicit per-catalog XMLTV URL setting and manual download; current/next titles and local start times on matched selected-catalog channels; no invented/name-guessed data",
        "formats": "Streaming UTF-8/UTF-16 XMLTV; gzip file/HTTP encoding, at most two layers; DTD never fetched, internal entities rejected",
        "limits": {"download_bytes": 8 * 1024 * 1024, "expanded_bytes": 32 * 1024 * 1024,
                   "parsed_programmes": 250000, "saved_programmes": 20000, "saved_characters": 2000000,
                   "cache_hours": 48, "stale_warning_hours": 24},
        "storage": "Private per-source URL preferences; bounded evictable guide cache with only a URL hash; derived alias sidecar, unchanged Room v1 and legacy channel identities",
        "network": "Normal shared HTTP/TLS/redirect policy, no media credentials or request logging; no periodic/background download; failed/cancelled refresh preserves same-feed good cache",
    },
    "stream_playback_tests": "Provider streams NOT TESTED; separate API31/API33 synthetic runtime workflow",
}
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
shutil.copyfile(root / "INSTALL-fa.md", output / "INSTALL-fa.md")
shutil.copyfile(root / "SIGNING-fa.md", output / "SIGNING-fa.md")
info["signature_verification"] = (build / "signature.txt").read_text()
# Public certificate digest from the APK actually supplied to the user, not a key.
previous_signer = "aa088f89aacd9f4e1e9642088ed7fa82dfeedc302c0ecb1e089effc34a365cb9"
current_signer = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", info["signature_verification"])
assert current_signer, "Missing verified signer fingerprint"
info["signer_sha256"] = current_signer.group(1)
# The APK must use our verified temporary key, never AGP's alternate default.
key_path = Path(os.environ["IPTV_TRIAL_KEYSTORE"]).resolve()
assert key_path.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve() / "iptv-private-signing")
assert certificate_digest(key_path) == current_signer.group(1), "APK signer differs from the prepared trial key"
info["signing_key_path_verified"] = True
info["private_signing_material_written_to_cache"] = False
info["legacy_signing_cache_restored"] = info["signing_source"] == "legacy-cache-transitional"
info["stable_secret_storage_active"] = info["signing_source"] == "actions-secret"
assert info["signing_source"] in {"actions-secret", "legacy-cache-transitional"}
info["updates_original_0_1_0_trial"] = current_signer.group(1) == previous_signer
previous_0_1_1_signer = "f73be34704f68399ada9b3a8d3d8ce82b585cd508beb68929e6a93063250da2a"
info["updates_delivered_0_1_1_trial"] = current_signer.group(1) == previous_0_1_1_signer
previous_0_1_2_signer = EXPECTED_SIGNER_SHA256
info["updates_delivered_0_1_2_trial"] = current_signer.group(1) == previous_0_1_2_signer
info["updates_delivered_0_1_3_trial"] = current_signer.group(1) == previous_0_1_2_signer
info["updates_delivered_0_1_4_trial"] = current_signer.group(1) == previous_0_1_2_signer
# Never silently deliver another incompatible trial after the user has saved local data.
assert info["updates_delivered_0_1_2_trial"], "Trial signer changed: stop; do not replace the user's installed app or erase data"
schema = json.loads((app / "schemas/io.github.fourkings6114.iptv.data.AppDatabase/1.json").read_text())["database"]
assert schema["version"] == 1
info["room_schema_version"] = schema["version"]
info["room_schema_identity_hash"] = schema["identityHash"]
info["source_management"] = "Independent URL/file catalog IDs; named imports; rename/refresh/transactional delete; legacy custom/file IDs retained"
info["installation_warning"] = "Verified same signer as delivered 0.1.2/0.1.3/0.1.4: install as an update without uninstalling or clearing data. Older 0.1.0/0.1.1 trials have different signers; stop and review rather than deleting needed local data. Retained trial identity; consult signing_source/stable_secret_storage_active and the owner backup guide. No new key is silently generated."
(output / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
publish("Android APK package details", 0, json.dumps(info, indent=2))
