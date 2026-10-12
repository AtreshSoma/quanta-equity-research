# CSV-Based Stock Universe Configuration

This directory contains CSV files for custom stock universes.

## Format

Each CSV must have at minimum a `symbol` column:

```csv
symbol,name,sector
BGRENERGY,BG Energy Systems,Energy
RELIANCE,Reliance Industries,Energy
```

## Files

- `custom_indian_stocks.csv` - Additional Indian stocks to include
- `custom_us_stocks.csv` - Additional US stocks to include

## Usage

The system automatically loads these CSV files with fallback to hardcoded lists if not found.

To add stocks:
1. Edit the appropriate CSV file
2. Add stock symbols (one per line)
3. Save the file
4. Run SP_ELITE_PRO - new stocks will be included

## Example

To add BGRENERGY to Indian scans:
1. Edit `custom_indian_stocks.csv`
2. Add line: `BGRENERGY,BG Energy Systems,Energy`
3. Save
4. Run a scan - BGRENERGY will be included
