# Directory Diff Scripts

This repository contains scripts to generate detailed diffs between two directories while ignoring whitespace differences and excluding certain folders like `node_modules`.

## Available Scripts

### 1. Basic Diff Script (`diff_directories.sh`)
- Performs a recursive diff between two directories
- Ignores whitespace differences
- Excludes `node_modules` and `.git` folders
- Provides basic output

### 2. Enhanced Diff Script (`diff_directories_enhanced.sh`)
- Same functionality as the basic script
- Includes colored output for better readability
- Provides summary statistics
- Shows files unique to each directory separately
- Shows side-by-side diff comparison

## Usage

### Basic Usage:
```bash
./diff_directories.sh <dir1> <dir2>
```

### Example:
```bash
./diff_directories.sh web/angular/throttler/dev web/angular/throttler/alt
```

### For the enhanced version:
```bash
./diff_directories_enhanced.sh web/angular/throttler/dev web/angular/throttler/alt
```

## Features

- **Whitespace Ignoring**: Uses the `-w` flag to ignore all whitespace differences
- **Folder Exclusion**: Automatically excludes `node_modules` and `.git` directories
- **Recursive Comparison**: Compares all files in subdirectories
- **Colored Output** (enhanced version): Makes it easier to distinguish different types of information

## Notes

- Both directories must exist for the script to run
- The script will show a message if no differences are found
- Files that exist in only one directory will be highlighted
- Differences in file content will be displayed in a side-by-side format in the enhanced version