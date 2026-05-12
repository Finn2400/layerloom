#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ggplot2)
  library(readr)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop("Usage: Rscript plot_simplified_speed_time.R <input_csv> <output_png> <output_pdf>")
}

input_csv <- args[[1]]
output_png <- args[[2]]
output_pdf <- args[[3]]

df <- read_csv(input_csv, show_col_types = FALSE)
df$arrangement_label <- factor(df$arrangement_label, levels = df$arrangement_label)
df$label_text <- df$total_time_text

bar_color <- "#266B8B"
axis_color <- "#000000"

p <- ggplot(df, aes(x = arrangement_label, y = total_time_hr)) +
  geom_col(width = 0.80, fill = bar_color, color = "#13455d", linewidth = 0.6) +
  geom_text(
    aes(label = label_text),
    vjust = -0.55,
    size = 6.2,
    color = "black",
    fontface = "plain"
  ) +
  scale_y_continuous(
    expand = expansion(mult = c(0, 0.06))
  ) +
  labs(
    x = NULL,
    y = "Print Duration"
  ) +
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

ggsave(output_png, p, width = 12.5, height = 7.5, dpi = 300, bg = "white")
ggsave(output_pdf, p, width = 12.5, height = 7.5, bg = "white")
