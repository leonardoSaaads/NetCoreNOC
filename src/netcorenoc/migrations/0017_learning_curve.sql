-- v0.19.0 — the learning curve, recorded.
--
-- `challenger_run` has kept `iterations` as a COUNT since v0.9.0 and nothing else about the fit's
-- progress, so the console could not answer the first question anyone asks of a trained model —
-- *is it learning?* — and the Judge screen said so in a paragraph headed "Not drawn, because
-- nothing measures it". This is that measurement. The paragraph goes; the chart arrives.
--
-- `loss_trace` is a JSON array of weighted mean negative log-likelihoods, oldest first, one every
-- `loss_trace_stride` iterations of the fit plus a final point at the coefficients that were
-- actually kept. JSON rather than a `challenger_loss(run_id, iteration, loss)` child table: the
-- series is read only as a whole, it is bounded at ~21 points by `training.TRACE_POINTS`, and a
-- child table would invite per-iteration queries of something that is one chart.
--
-- NULL for every existing row, and that is the honest value: those runs were not measured, and
-- back-filling a curve for a fit whose intermediate states nobody recorded would be inventing it.
-- The console draws a curve where there is one and says the run predates the measurement where
-- there is not.
ALTER TABLE challenger_run ADD COLUMN loss_trace TEXT;
ALTER TABLE challenger_run ADD COLUMN loss_trace_stride INTEGER;
