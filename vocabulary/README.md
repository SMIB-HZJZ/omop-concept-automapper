# OMOP Vocabulary Files

Place your OMOP vocabulary files in this directory. The backend and worker containers mount it read-only at `/data/vocabulary` (`VOCABULARY_DIR`).

## Required Files

The vocabulary import expects these files:

1. **CONCEPT.csv**
2. **CONCEPT_RELATIONSHIP.csv**
3. **CONCEPT_ANCESTOR.csv**

Optional:

4. **DRUG_STRENGTH.csv**: enables deterministic drug mapping by ingredient, strength and dose form
   (the `automap_drug` pipeline). Without it, drugs are mapped by similarity search and the LLM.

They must be in the Athena download format: tab-delimited, with a header row. Missing files are skipped and reported.

## Usage

1. Download the OMOP vocabulary files from [OHDSI Athena](https://athena.ohdsi.org/).
2. Place the CSV files in this directory.
3. Sign in as an admin and open **Import Data → OMOP Vocabulary Tables**, which shows whether each file was found and its size.
4. Click **Import OMOP Vocabulary**. The import runs in the background, and you can follow its progress in the task tray.
5. Afterwards, run **ATC7 Processing** and embed the standard concepts (**Embedding Management**).
