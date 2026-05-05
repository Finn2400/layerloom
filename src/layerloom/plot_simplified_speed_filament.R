#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ggplot2)
  library(readr)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript plot_simplified_speed_filament.R <input_csv> <total_png> <total_pdf> <ratio_png> <ratio_pdf>")
}

input_csv <- args[[1]]
total_png <- args[[2]]
total_pdf <- args[[3]]
ratio_png <- args[[4]]
ratio_pdf <- args[[5]]

df <- read_csv(input_csv, show_col_types = FALSE)
df$arrangement_label <- factor(df$arrangement_label, levels = df$arrangement_label)
df$total_label <- sprintf("%.1f g", round(df$total_g, 1))

bar_color <- "#266B8B"
axis_color <- "#000000"

make_plot <- function(data, y_col, y_label) {
  ggplot(data, aes(x = arrangement_label, y = .data[[y_col]])) +
    geom_col(width = 0.80, fill = bar_color, color = "#13455d", linewidth = 0.6) +
    {
      if (y_col == "total_g") {
        geom_text(
          data = data,
          aes(label = total_label),
          vjust = -0.55,
          size = 6.0,
          color = "black",
          fontface = "plain"
        )
      }
    } +
    scale_y_continuous(expand = expansion(mult = c(0, 0.06))) +
    labs(x = NULL, y = y_label) +
    theme_minimal(base_size = 24) +
    theme(
      panel.grid = element_blank(),
      panel.background = element_rect(fill = "white", color = NA),
      plot.background = element_rect(fill = "white", color = NA),
      axis.line.x = element_line(color = axis_color, linewidth = 2.6),
      axis.line.y = element_line(color = axis_color, linewidth = 2.6),
      axis.ticks.x = element_blank(),
      axis.ticks.y = element_line(color = axis_color, linewidth = 1.2),
      axis.ticks.length.y = grid::unit(0.18, "cm"),
      axis.text.y = element_text(size = 18, color = "black", margin = margin(r = 8)),
      axis.title.y = element_text(size = 30, color = "black", margin = margin(r = 18)),
      axis.text.x = element_text(size = 22, color = "black", margin = margin(t = 14)),
      plot.margin = margin(20, 30, 20, 20)
    )
}

p_total <- make_plot(df, "total_g", "Total Filament Used")
p_ratio <- NULL
has_ratio <- "model_to_waste_ratio" %in% names(df) &&
  any(!is.na(df$model_to_waste_ratio))
if (has_ratio) {
  p_ratio <- make_plot(df, "model_to_waste_ratio", "Model / Waste Ratio")
}

ggsave(total_png, p_total, width = 12.5, height = 7.5, dpi = 300, bg = "white")
ggsave(total_pdf, p_total, width = 12.5, height = 7.5, bg = "white")

if (!is.null(p_ratio)) {
  ggsave(ratio_png, p_ratio, width = 12.5, height = 7.5, dpi = 300, bg = "white")
  ggsave(ratio_pdf, p_ratio, width = 12.5, height = 7.5, bg = "white")
}
