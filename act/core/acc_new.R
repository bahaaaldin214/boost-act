#!/usr/bin/env Rscript

# Usage: Rscript new_gg.R --project_dir "/Shared/vosslabhpc/Projects/BOOST/InterventionStudy/3-experiment/data/act-int-test/" --deriv_dir "derivatives/GGIR-3.2.6/"
library(optparse)
library(GGIR)

main <- function() {
  # Define the option list
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
                help = "Path to the derivatives directory relative to output_dir", metavar = "character")
  )

  # Parse the options
  opt_parser <- OptionParser(option_list = option_list)
  opt <- parse_args(opt_parser)

  # Resolve Paths
  # Priority: --input_dir > --project_dir > default
  InputDir <- opt$input_dir
  if (is.null(InputDir)) {
    InputDir <- opt$project_dir
  }
  if (is.null(InputDir)) {
    InputDir <- "/mnt/nfs/lss/vosslabhpc/Projects/BOOST/InterventionStudy/3-Experiment/data/act-int-test/"
  }

  # Priority: --output_dir > InputDir
  OutputDir <- opt$output_dir
  if (is.null(OutputDir)) {
    OutputDir <- InputDir
  }

  ProjectDerivDir <- opt$deriv_dir
  last_folder <- basename(InputDir)
  
  # Determine correct filename
  if (grepl("act-obs", last_folder, fixed = TRUE)) {
    SleepLog <- normalizePath(file.path(InputDir, "sleep_log_observational.csv"), mustWork = FALSE)
  } else if (grepl("act-int", last_folder, fixed = TRUE)) {
    SleepLog <- normalizePath(file.path(InputDir, "sleep_log_intervention.csv"), mustWork = FALSE)
  } else if (grepl("input", last_folder, fixed = TRUE)) {
    # Default to intervention if generic 'input' name
    SleepLog <- normalizePath(file.path(InputDir, "sleep_log_intervention.csv"), mustWork = FALSE)
  } else {
    # Fallback to current dir if unrecognized
    SleepLog <- normalizePath(file.path(InputDir, "sleep_log_intervention.csv"), mustWork = FALSE)
  }

  print(paste("Input Directory:", InputDir))
  print(paste("Output Directory:", OutputDir))
  print(paste("Derivatives Directory:", ProjectDerivDir))
  print(paste("Sleep Log Location:", SleepLog))

  # Helper functions
  SubjectGGIRDeriv <- function(x) {
    # x is a path relative to InputDir
    a <- dirname(x)
    # Output path construction
    file.path(OutputDir, ProjectDerivDir, a)
  }

  datadirname <- function(x) {
    # x is a path relative to InputDir
    b <- dirname(x)
    file.path(InputDir, b)
  }

  # Gather subject directories
  directories <- list.dirs(InputDir, recursive = FALSE)
  subdirs <- directories[grepl("sub-*", directories)]
  print(paste("subdirs found: ", length(subdirs)))

  # Create project-specific derivatives GGIR folder if it doesn't exist
  FinalDerivPath <- file.path(OutputDir, ProjectDerivDir)
  if (!dir.exists(FinalDerivPath)) {
    dir.create(FinalDerivPath, recursive = TRUE)
  }

  # List accel files: prefer gt3x when both formats exist for one session.
  all_candidates <- list.files(
    subdirs,
    pattern = "\\.(gt3x|csv)$",
    recursive = TRUE,
    include.dirs = FALSE,
    full.names = TRUE,
    no.. = TRUE
  )

  print(paste("Files found: ", length(all_candidates)))

  # Normalize paths relative to InputDir for processing.
  InputDirAbs <- normalizePath(InputDir, winslash = "/", mustWork = FALSE)
  GGIRfilesAbs <- normalizePath(all_candidates, winslash = "/", mustWork = FALSE)

  # Strip the InputDir part to get relative paths.
  RelativeFiles <- ifelse(
    startsWith(GGIRfilesAbs, paste0(InputDirAbs, "/")),
    substring(GGIRfilesAbs, nchar(InputDirAbs) + 2),
    GGIRfilesAbs
  )

  # Keep one file per session directory. gt3x wins over csv.
  candidate_groups <- split(RelativeFiles, dirname(RelativeFiles))
  GGIRfiles <- unlist(lapply(candidate_groups, function(paths) {
    gt3x <- paths[grepl("\\.gt3x$", paths, ignore.case = TRUE)]
    if (length(gt3x) > 0) {
      return(gt3x[1])
    }

    csv <- paths[grepl("_accel\\.csv$", paths, ignore.case = TRUE)]
    if (length(csv) > 0) {
      return(csv[1])
    }

    character(0)
  }), use.names = FALSE)
  
  # Ensure directory structure exists in Output
  for (i in RelativeFiles) {
    target_deriv <- SubjectGGIRDeriv(i)
    if (!dir.exists(target_deriv)) {
      dir.create(target_deriv, recursive = TRUE)
    }
  }

  # Run GGIR loop
  # Note: If a session has both .gt3x and .csv, GGIR will process both if they are in different subdirs.
  # Since our BIDS layout puts them in the same subdir, we should be careful.
  for (r in RelativeFiles) {
    datadir <- datadirname(r)
    outputdir <- SubjectGGIRDeriv(r)
    
    print(paste("Processing: ", r))
    print(paste("datadir: ", datadir))
    print(paste("outputdir: ", outputdir))
    
    if (!dir.exists(datadir)) {
      print(paste("Error: datadir does not exist ->", datadir))
      next
    }

    try({
      GGIR(
        # ==== Initialization ====
        mode = 1:6,
        datadir = datadir,
        outputdir = outputdir,
        studyname = "boost",
        overwrite = TRUE,
        desiredtz = "America/Chicago",
        print.filename = TRUE,
        idloc = 6,

        # ==== Part 1: Data loading and basic signal processing ====
        do.report = c(2, 4, 5, 6),
        epochvalues2csv = TRUE,
        acc.metric = "ENMO",
        windowsizes = c(5, 900, 3600),

        # ==== Part 2: Non-wear detection ====
        ignorenonwear = TRUE,

        # ==== Part 3: Sleep detection ====
       #loglocation = SleepLog,
       #colid = 1,
       #coln1 = 2,
       #sleepwindowType = "TimeInBed",
       #imputeTimegaps = TRUE, # since idle sleep mode is on for actigraph devices

        # ==== Part 4: Physical activity summaries ====
        timewindow = c("WW", "MM", "OO"),

        # ==== Part 5: Day-level summaries ====
        hrs.del.start = 4,
        hrs.del.end = 3,
        maxdur = 9,
        threshold.lig = 44.8,
        threshold.mod = 100.6,
        threshold.vig = 428.8,

        # ==== Part 6: CR and other metrics ====
        part6CR = TRUE,
        visualreport = TRUE,
        old_visualreport = FALSE
      )
    })
  }
}

# Run main if executed as script
if (!interactive()) {
  main()
}
