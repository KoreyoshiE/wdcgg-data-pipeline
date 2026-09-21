# Data availability

## WDCGG observations

Raw WDCGG archives and extracted observation files are not distributed in this repository. They must be obtained from the [World Data Centre for Greenhouse Gases](https://gaw.kishou.go.jp/) under the applicable provider terms. The frozen source identities and SHA-256 hashes used by the study are recorded in `provenance/DOWNLOAD_MANIFEST_V2.csv`.

Freshly downloaded provider files may differ from the frozen study versions. Treat a changed DOI, dataset version, coverage period or archive hash as source-version drift rather than an exact reproduction.

## Included metadata and fixtures

- `data/manifests/` contains the public catalogue/metadata snapshots used for screening and a Natural Earth map geometry file.
- `data/examples/` contains small synthetic files for parser and QC tests. They are not observations from a real station.
- `results/` contains selected machine-readable summaries produced by the study; it does not contain raw concentration records.

## Third-party material

WDCGG data, station metadata, Natural Earth geometry and cited publications remain subject to their original licenses and attribution requirements. The repository's MIT License applies to the project source code, not to those materials.
