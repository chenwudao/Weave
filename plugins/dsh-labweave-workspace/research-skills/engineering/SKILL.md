---
name: weave-research-engineering
description: Reproducible experiment execution
---

# Reproducible experiment execution

Before execution, record the research question, baseline, planned comparison, environment, command, data version, seed and success metric as applicable. Respect project approval and tool visibility. Register the run with experiment_record and update the same ID with actual logs, status, measured metrics and project-relative output paths. Place deliverables under outputs. Use real metric data and units for charts, and report uncertainty, failed runs and reproduction limits.

On a sequence, use dependsOn for actual gates such as baseline completion before comparison. In a matrix, use category for the method or configuration and keep independent runs parallel. Use relatedIds for runs compared on the same metric where that link helps readers inspect the comparison.

Before retaining a canvas draft or submitting a result, read the relevant Bot cards and run records. For the same run, check the configuration, data version, seed, metric units and status. Keep the registered run as the source for its logs, measurements and artifacts. Reference that run and retain a separate process draft only for distinct work such as a diagnostic step, an open discrepancy or a failed attempt with its own evidence. If a draft disagrees with verified metrics or run status, name the run, document the evidence and mark the discrepancy pending review before proposing any revision.
