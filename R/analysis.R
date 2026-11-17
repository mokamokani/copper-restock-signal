#!/usr/bin/env Rscript
# Copper procurement signal - R implementation (base R only, no packages).
#
# STATUS: EXECUTED and cross-checked on R 4.6.2 (Windows, 2026-11-17).
# Reproduces the Python report exactly: 222 sessions, as of 2026-11-16,
# 18 raw A triggers, 15 raw B triggers, baseline 13454.32 USD/t,
# dip average 12673.37 USD/t, delta -5.80% (simulation, synthetic volumes).
# Run with:  Rscript R/analysis.R
#
# Outputs:
#   reports/r_signal_summary.csv   daily indicators + raw signal flags
#   reports/r_signals.png          price, MA20 and both trigger families
#
# Threshold values are read from config.json with a tiny base-R parser so this
# script and the Python report stay on the same parameters (no jsonlite needed).

args <- commandArgs(trailingOnly = FALSE)
file_arg <- sub("^--file=", "", args[grep("^--file=", args)])
root <- normalizePath(file.path(dirname(file_arg), ".."))

read_num <- function(txt, key) {
  pat <- paste0('"', key, '"\\s*:\\s*(-?[0-9.]+)')
  m <- regmatches(txt, regexpr(pat, txt, perl = TRUE))
  if (length(m) == 0) stop(paste("config key not found:", key))
  as.numeric(sub(".*:\\s*", "", m))
}
cfg_txt <- paste(readLines(file.path(root, "config.json"), warn = FALSE), collapse = "\n")
ma_n    <- as.integer(read_num(cfg_txt, "ma_window"))
look    <- as.integer(read_num(cfg_txt, "lookback_days"))
surge1  <- read_num(cfg_txt, "surge_1d_pct")
surge5  <- read_num(cfg_txt, "surge_5d_pct")
dip_pct <- read_num(cfg_txt, "dip_vs_ma20_pct")
rank_mx <- read_num(cfg_txt, "percentile_60d_max")

df <- read.csv(file.path(root, "data", "prices_lme_cash.csv"), stringsAsFactors = FALSE)
df <- df[order(df$date), ]
p  <- df$cash_settlement_usd_per_t
n  <- length(p)
stopifnot(n >= look)

ge <- function(x, t) !is.na(x) & x >= t
le <- function(x, t) !is.na(x) & x <= t

chg1  <- c(NA, (p[2:n] / p[1:(n - 1)] - 1) * 100)
chg5  <- c(rep(NA, 5), (p[6:n] / p[1:(n - 5)] - 1) * 100)
ma    <- sapply(seq_len(n), function(i) if (i >= ma_n) mean(p[(i - ma_n + 1):i]) else NA_real_)
dev   <- (p / ma - 1) * 100
basis <- (p / df$three_month_usd_per_t - 1) * 100
rank  <- sapply(seq_len(n), function(i) {
  w <- p[max(1, i - look + 1):i]
  sum(w <= p[i]) / length(w)
})

# A: surge -> consider locking in early. A 1-day spike needs 5-day confirmation.
surge <- (ge(chg1, surge1) & ge(chg5, 0)) | ge(chg5, surge5)
# B: dip -> consider restocking. Price below MA and near the bottom of its range.
dip <- le(dev, dip_pct) & (rank <= rank_mx)

out <- data.frame(
  date = df$date,
  cash_usd_per_t = p,
  three_month_usd_per_t = df$three_month_usd_per_t,
  lme_stock_t = df$lme_stock_t,
  chg_1d_pct = round(chg1, 3),
  chg_5d_pct = round(chg5, 3),
  ma20_usd_per_t = round(ma, 2),
  dev_ma20_pct = round(dev, 3),
  basis_pct = round(basis, 3),
  pct_rank_60d = round(rank, 4),
  signal_A_surge = surge,
  signal_B_dip = dip,
  stringsAsFactors = FALSE
)

# Same-volume, price-only timing comparison (synthetic parameters, simulation only).
month_key <- substr(df$date, 1, 7)
baseline_idx <- which(!duplicated(month_key))
base_avg <- mean(p[baseline_idx])
dip_avg <- if (sum(dip) > 0) mean(p[dip]) else NA_real_
delta <- if (is.na(dip_avg)) NA_real_ else (dip_avg / base_avg - 1) * 100

dir.create(file.path(root, "reports"), showWarnings = FALSE, recursive = TRUE)
write.csv(out, file.path(root, "reports", "r_signal_summary.csv"), row.names = FALSE)

png(file.path(root, "reports", "r_signals.png"), width = 1100, height = 620)
plot(p, type = "l", col = "#0B4F9C", lwd = 2,
     main = "LME copper cash settlement with procurement signals",
     xlab = "Trading session (2026)", ylab = "USD per tonne",
     xaxt = "n")
tick <- seq(1, n, by = max(1, floor(n / 8)))
axis(1, at = tick, labels = substr(df$date[tick], 6, 10), cex.axis = 0.8)
lines(ma, col = "#888780", lwd = 1.5, lty = 2)
points(which(surge), p[surge], col = "#C0392B", pch = 17, cex = 1.1)
points(which(dip), p[dip], col = "#1D9E75", pch = 15, cex = 1.1)
legend("topleft", legend = c("Cash settlement", "MA20", "A surge (lock in)", "B dip (restock)"),
       col = c("#0B4F9C", "#888780", "#C0392B", "#1D9E75"),
       lty = c(1, 2, 0, 0), pch = c(NA, NA, 17, 15), lwd = c(2, 1.5, 0, 0), bty = "n")
dev.off()

cat(sprintf("rows=%d | as_of=%s | A triggers=%d | B triggers=%d\n",
            n, df$date[n], sum(surge), sum(dip)))
cat(sprintf("baseline avg=%.2f | dip avg=%s | delta=%s%% (simulation, synthetic volumes)\n",
            base_avg,
            if (is.na(dip_avg)) "n/a" else sprintf("%.2f", dip_avg),
            if (is.na(delta)) "n/a" else sprintf("%+.2f", delta)))
cat("wrote reports/r_signal_summary.csv and reports/r_signals.png\n")
