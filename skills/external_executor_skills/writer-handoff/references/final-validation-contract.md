# Final Validation Contract

Writer Handoff validates six surfaces in one snapshot.

## Executor status

The status file is parseable, terminal, not ResearchOS-accepted, and consistent with the result-pack outcome.

## Result pack

The final pack is parseable and contains the scientific sections required by its terminal outcome. A completed outcome cannot omit experiment runs, implementation/review, diagnosis, attribution, realized method, framework, figure/table inventory, or evidence packaging.

## Run manifest

Every artifact path remains inside the workspace, exists, and matches declared checksum and size. Duplicate paths are warnings. Missing, escaping, or mismatched paths are blocking.

## Research report

The report is nonempty, contains all eight sections and required table columns, and includes every normalized experiment, result, Claim, source path, final asset, and verified literature identifier. Quantitative records without raw paths are blocking.

## Figures

Recursively inspect `external_executor/figure/`. Every final file is nonempty, unchanged since snapshot, registered in the manifest, and listed in the report Artifact Index.

## Tables

Apply the same rules recursively to `external_executor/table/`. Final tables are authoritative numeric presentation sources, not substitutes for linked raw results.

The final validation report itself remains under `external_executor/report/phase_F/`. Do not move this responsibility back to the root skill or satisfy it by checking file existence alone.
