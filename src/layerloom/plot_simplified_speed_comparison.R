#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ggplot2)
  library(readr)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript plot_simplified_speed_comparison.R <input_csv> <time_png> <time_pdf> <filament_png> <filament_pdf>")
}

input_csv <- args[[1]]
time_png <- args[[2]]
time_pdf <- args[[3]]
filament_png <- args[[4]]
filament_pdf <- args[[5]]

df <- read_csv(input_csv, show_col_types = FALSE)
df$arrangement_label <- factor(df$arrangement_label, levels = c("A1", "A2", "A3", "A4", "A5"))
df$slicer <- factor(df$slicer, levels = c("Orca", "Prusa", "Bambu"))
df$time_label <- ifelse(
  df$slicer == "Orca",
  df$total_time_text,
  ifelse(
    df$slicer == "Prusa",
    df$total_time_text,
    df$total_time_text
  )
)
df$filament_label <- ifelse(
  df$slicer == "Orca",
  sprintf("%.1f g", round(df$total_g, 1)),
  ifelse(
    df$slicer == "Prusa",
    sprintf("%.1f g", round(df$total_g, 1)),
    sprintf("%.1f g", round(df$total_g, 1))
  )
)

bar_fill <- c("Orca" = "#27B3B0", "Prusa" = "#F97316", "Bambu" = "#22C55E")
bar_edge <- "#1B1B1B"
axis_color <- "#000000"

make_plot <- function(data, y_col, y_label, label_col) {
  ggplot(data, aes(x = arrangement_label, y = .data[[y_col]], fill = slicer)) +
    geom_col(
      position = position_dodge(width = 0.82),
      width = 0.72,
      color = bar_edge,
      linewidth = 0.6
    ) +
    geom_text(
      aes(label = .data[[label_col]]),
      position = position_dodge(width = 0.82),
      vjust = -0.45,
      size = 4.4,
      lineheight = 1.0,
      color = "black",
      fontface = "plain"
    ) +
    scale_fill_manual(values = bar_fill) +
    scale_y_continuous(expand = expansion(mult = c(0, 0.16))) +
    labs(x = NULL, y = y_label, fill = NULL) +
    theme_minimal(base_size = 22) +
    theme(
      panel.grid = element_blank(),
      panel.background = element_rect(fill = "white", color = NA),
      plot.background = element_rect(fill = "white", color = NA),
      axis.line.x = element_line(color = axis_color, linewidth = 2.4),
      axis.line.y = element_line(color = axis_color, linewidth = 2.4),
      axis.ticks.x = element_blank(),
      axis.ticks.y = element_line(color = axis_color, linewidth = 1.1),
      axis.ticks.length.y = grid::unit(0.16, "cm"),
      axis.text.y = element_text(size = 16, color = "black", margin = margin(r = 8)),
      axis.title.y = element_text(size = 28, color = "black", margin = margin(r = 16)),
      axis.text.x = element_text(size = 20, color = "black", margin = margin(t = 12)),
      legend.position = "top",
      legend.text = element_text(size = 16, color = "black"),
      plot.margin = margin(20, 30, 20, 20)
    )
}

p_time <- make_plot(df, "total_time_hr", "Print Duration", "time_label")
p_filament <- make_plot(df, "total_g", "Total Filament Used", "filament_label")

ggsave(time_png, p_time, width = 13.5, height = 7.8, dpi = 300, bg = "white")
ggsave(time_pdf, p_time, width = 13.5, height = 7.8, bg = "white")
ggsave(filament_png, p_filament, width = 13.5, height = 7.8, dpi = 300, bg = "white")
ggsave(filament_pdf, p_filament, width = 13.5, height = 7.8, bg = "white")
