# Data availability

## WDCGG observations

Raw WDCGG archives and extracted observation files are not distributed in this repository. They must be obtained from the [World Data Centre for Greenhouse Gases](https://gaw.kishou.go.jp/) under the applicable provider terms. The frozen source identities and SHA-256 hashes used by the study are recorded in `provenance/DOWNLOAD_MANIFEST_V2.csv`.

Place the required `.tar.gz` archives in a user-controlled directory supplied through `--data-root`. Files may be organised in subdirectories; the reproduction command indexes them recursively by SHA-256. Keep this directory outside the repository. The study manifest lists 38 required archives and their recorded sizes and hashes. Reproduction verifies archive hashes and the extracted observation-file hashes before analyses proceed; a changed archive container is reported separately from an unchanged observation member.

Freshly downloaded provider files may differ from the frozen study versions. Treat a changed DOI, dataset version, coverage period or archive hash as source-version drift rather than an exact reproduction.

## Included metadata and fixtures

- `data/manifests/` contains the public catalogue/metadata snapshots used for screening and a Natural Earth map geometry file.
- `data/examples/` contains small synthetic files for parser and QC tests. They are not observations from a real station.
- `results/` contains selected machine-readable summaries produced by the study; it does not contain raw concentration records.

## Third-party material

WDCGG data, station metadata, Natural Earth geometry and cited publications remain subject to their original licenses and attribution requirements. The repository's MIT License applies to the project source code, not to those materials.

The compact tables in `results/frozen/` are derived summaries. They do not license redistribution of the underlying hourly records, and they cannot substitute for the official source archives in a full reproduction.
