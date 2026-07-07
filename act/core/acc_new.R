#!/usr/bin/env Rscript

# Usage: Rscript act/core/acc_new.R --input_dir <bids-root> --output_dir <out-root> --deriv_dir derivatives/GGIR-3.2.6/
library(optparse)
library(GGIR)

resolve_session_context <- function(relative_file, input_dir) {
  parts <- strsplit(relative_file, "/", fixed = TRUE)[[1]]
  ses_idx <- which(grepl("^ses-", parts))
  if (length(ses_idx) == 0) {
    stop("No ses-* directory in relative path: ", relative_file)
  }
  ses_i <- ses_idx[length(ses_idx)]
  session_name <- parts[ses_i]
  session_rel <- paste(parts[seq_len(ses_i)], collapse = "/")
  list(
    session_name = session_name,
    session_rel = session_rel,
    datafile = file.path(input_dir, relative_file)
  )
}

resolve_sleep_log <- function(input_dir, layout = "auto") {
  candidates <- c(
    file.path(input_dir, "sleep_log_extend.csv"),
    file.path(input_dir, "sourcedata", "sleep_logs", "sleep_log_extend.csv"),
    file.path(input_dir, "sleep_log_intervention.csv"),
    file.path(input_dir, "sleep_log_observational.csv")
  )
  for (candidate in candidates) {
    if (file.exists(candidate)) {
      return(normalizePath(candidate, winslash = "/", mustWork = FALSE))
    }
  }

  last_folder <- basename(normalizePath(input_dir, winslash = "/", mustWork = FALSE))
  if (layout == "extend" || grepl("BikeExtend", input_dir, fixed = TRUE)) {
    return(normalizePath(file.path(input_dir, "sleep_log_extend.csv"), winslash = "/", mustWork = FALSE))
  }
  if (grepl("act-obs", last_folder, fixed = TRUE)) {
    return(normalizePath(file.path(input_dir, "sleep_log_observational.csv"), winslash = "/", mustWork = FALSE))
  }
  normalizePath(file.path(input_dir, "sleep_log_intervention.csv"), winslash = "/", mustWork = FALSE)
}

detect_layout <- function(relative_files, input_dir) {
  if (any(grepl("ses-accel", relative_files, fixed = TRUE))) {
    return("extend")
  }
  if (any(grepl("/accel/ses-", relative_files, fixed = TRUE))) {
    return("boost")
  }
  if (grepl("BikeExtend", input_dir, fixed = TRUE)) {
    return("extend")
  }
  "boost"
}

main <- function() {
  option_list <- list(
    make_option(c("-p", "--project_dir"), type = "character",
                default = NULL,
                help = "Legacy: Path to the project directory containing sub-* folders", metavar = "character"),
    make_option(c("-i", "--input_dir"), type = "character",
                default = NULL,
                help = "Path to the input directory containing sub-* folders", metavar = "character"),
    make_option(c("-o", "--output_dir"), type = "character",
                default = NULL,
                help = "Path to the output directory where derivatives will be saved", metavar = "character"),
    make_option(c("-d", "--deriv_dir"), type = "character",
                default = "derivatives/GGIR-3.2.6/",
                help = "Path to the derivatives directory relative to output_dir", metavar = "character"),
    make_option(c("-l", "--layout"), type = "character",
                default = Sys.getenv("GGIR_LAYOUT", "auto"),
                help = "Input layout: auto, boost, or extend", metavar = "character"),
    make_option(c("-s", "--sleep_log"), type = "character",
                default = Sys.getenv("GGIR_SLEEP_LOG", ""),
                help = "Optional explicit sleep log CSV path", metavar = "character")
  )

  opt_parser <- OptionParser(option_list = option_list)
  opt <- parse_args(opt_parser)

  InputDir <- opt$input_dir
  if (is.null(InputDir)) {
    InputDir <- opt$project_dir
  }
  if (is.null(InputDir)) {
    InputDir <- "/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-Experiment/data/act-int-test/"
  }

  OutputDir <- opt$output_dir
  if (is.null(OutputDir)) {
    OutputDir <- InputDir
  }

  ProjectDerivDir <- opt$deriv_dir
  InputDirAbs <- normalizePath(InputDir, winslash = "/", mustWork = FALSE)
  OutputDirAbs <- normalizePath(OutputDir, winslash = "/", mustWork = FALSE)

  directories <- list.dirs(InputDirAbs, recursive = FALSE, full.names = TRUE)
  subdirs <- directories[grepl("sub-", basename(directories), fixed = TRUE)]
  print(paste("subdirs found: ", length(subdirs)))

  FinalDerivPath <- file.path(OutputDirAbs, ProjectDerivDir)
  if (!dir.exists(FinalDerivPath)) {
    dir.create(FinalDerivPath, recursive = TRUE)
  }

  all_candidates <- list.files(
    subdirs,
    pattern = "\\.(gt3x|csv)$",
    recursive = TRUE,
    include.dirs = FALSE,
    full.names = TRUE,
    no.. = TRUE
  )
  print(paste("Files found: ", length(all_candidates)))

  GGIRfilesAbs <- normalizePath(all_candidates, winslash = "/", mustWork = FALSE)
  RelativeFiles <- ifelse(
    startsWith(GGIRfilesAbs, paste0(InputDirAbs, "/")),
    substring(GGIRfilesAbs, nchar(InputDirAbs) + 2),
    GGIRfilesAbs
  )

  layout <- opt$layout
  if (identical(layout, "auto")) {
    layout <- detect_layout(RelativeFiles, InputDirAbs)
  }
  print(paste("Layout:", layout))

  prefer_raw <- isTRUE(as.logical(Sys.getenv("GGIR_PREFER_RAW", "FALSE")))
  candidate_groups <- split(RelativeFiles, dirname(RelativeFiles))
  GGIRfiles <- unlist(lapply(candidate_groups, function(paths) {
    gt3x <- paths[grepl("\\.gt3x$", paths, ignore.case = TRUE)]
    csv <- paths[grepl("_accel\\.csv$", paths, ignore.case = TRUE)]
    if (layout == "extend" && length(csv) == 0) {
      csv <- paths[grepl("\\.csv$", paths, ignore.case = TRUE)]
    }

    if (prefer_raw) {
      if (length(csv) > 0) return(csv[1])
      if (length(gt3x) > 0) return(gt3x[1])
    } else {
      if (length(gt3x) > 0) return(gt3x[1])
      if (length(csv) > 0) return(csv[1])
    }

    character(0)
  }), use.names = FALSE)

  SleepLog <- opt$sleep_log
  if (is.null(SleepLog) || !nzchar(SleepLog)) {
    SleepLog <- resolve_sleep_log(InputDirAbs, layout = layout)
  } else {
    SleepLog <- normalizePath(SleepLog, winslash = "/", mustWork = FALSE)
  }

  use_sleep_log <- isTRUE(as.logical(Sys.getenv("GGIR_USE_SLEEP_LOG", "FALSE")))
  if (layout == "extend") {
    use_sleep_log <- isTRUE(as.logical(Sys.getenv("GGIR_USE_SLEEP_LOG", "TRUE")))
  }
  if (file.exists(SleepLog)) {
    use_sleep_log <- TRUE
  }

  print(paste("Input Directory:", InputDirAbs))
  print(paste("Output Directory:", OutputDirAbs))
  print(paste("Derivatives Directory:", ProjectDerivDir))
  print(paste("Sleep Log Location:", SleepLog))
  print(paste("Use Sleep Log:", use_sleep_log))

  session_output_dir <- function(session_rel, session_name) {
    file.path(OutputDirAbs, ProjectDerivDir, session_rel, paste0("output_", session_name))
  }

  for (relative_file in GGIRfiles) {
    ctx <- resolve_session_context(relative_file, InputDirAbs)
    target_deriv <- session_output_dir(ctx$session_rel, ctx$session_name)
    if (!dir.exists(target_deriv)) {
      dir.create(target_deriv, recursive = TRUE)
    }
  }

  process_one <- function(r) {
    ctx <- resolve_session_context(r, InputDirAbs)
    datafile <- ctx$datafile
    studyname <- ctx$session_name
    outputdir <- session_output_dir(ctx$session_rel, ctx$session_name)

    print(paste("Processing: ", r))
    print(paste("datafile: ", datafile))
    print(paste("outputdir: ", outputdir))
    print(paste("studyname: ", studyname))

    if (!file.exists(datafile)) {
      print(paste("Error: datafile does not exist ->", datafile))
      return(invisible(NULL))
    }

    ggir_args <- list(
      mode = 1:6,
      datadir = datafile,
      outputdir = outputdir,
      studyname = studyname,
      overwrite = isTRUE(as.logical(Sys.getenv("GGIR_OVERWRITE", "TRUE"))),
      desiredtz = "America/Chicago",
      print.filename = TRUE,
      idloc = 6,
      do.report = c(2, 4, 5, 6),
      epochvalues2csv = TRUE,
      acc.metric = "ENMO",
      windowsizes = c(5, 900, 3600),
      ignorenonwear = TRUE,
      timewindow = c("WW", "MM", "OO"),
      hrs.del.start = 4,
      hrs.del.end = 3,
      maxdur = 9,
      threshold.lig = 44.8,
      threshold.mod = 100.6,
      threshold.vig = 428.8,
      part6CR = TRUE,
      visualreport = TRUE,
      old_visualreport = FALSE
    )

    if (use_sleep_log && file.exists(SleepLog)) {
      ggir_args$loglocation <- SleepLog
      ggir_args$colid <- 1
      ggir_args$coln1 <- 2
      ggir_args$sleepwindowType <- "TimeInBed"
      ggir_args$imputeTimegaps <- TRUE
    }

    try(do.call(GGIR, ggir_args))
  }

  ncores <- as.integer(Sys.getenv("GGIR_NCORES", "3"))
  if (is.na(ncores) || ncores < 1) ncores <- 1L
  parallel::mclapply(GGIRfiles, process_one, mc.cores = ncores)
}

if (!interactive()) {
  main()
}
