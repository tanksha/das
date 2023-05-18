import os
import glob
import csv
import json
import re

class Table:

    def __init__(self, name):
        self.header = None
        self.rows = []
        self.values = {}
        self.name = name
        self.covered_by = {}
        self.mapped_fields = set()
        self.unmapped_fields = set()
        self.mapping = {}
        self.flybase_id_re = re.compile("^(\S+:)?(FB[a-zA-Z]{2}[0-9]{5,10})$")

    def set_header(self, header):
        self.header = [h.strip() for h in header]
        for key in self.header:
            assert key
            self.values[key] = set()
            self.covered_by[key] = {}
            self.unmapped_fields.add(key)
        assert len(self.unmapped_fields) == len(self.header)

    def process_row_value(self, v):
        v = v.strip()
        m = self.flybase_id_re.search(v)
        if m is not None:
            v = m.group(2)
        return v

    def add_row(self, pre_row):
        row = [self.process_row_value(value) for value in pre_row]
        assert len(self.header) == len(row), f"header = {self.header} row = {row}"
        self.rows.append(row)
        for key, value in zip(self.header, row):
            if value:
                self.values[key].add(value)
                if value not in self.covered_by[key]:
                    self.covered_by[key][value] = set()

    def print_values(self):
        for key in self.values.keys():
            print(f"{key}: {self.values[key]}")

    def get_relevant_sql_tables(self):
        return set([sql_table for sql_table, _ in self.mapping.values()])

    def check_field_value(self, sql_table, sql_field, value):
        for key, values in self.values.items():
            if key in self.unmapped_fields and value in values:
                tag = tuple([key, value])
                sql_tag = tuple([sql_table, sql_field])
                self.covered_by[key][value].add(sql_tag)
                if all(sql_tag in s for s in self.covered_by[key].values()):
                    self.unmapped_fields.remove(key)
                    self.mapped_fields.add(key)
                    self.mapping[key] = sql_tag

    def check_near_match(self):
        finished = []
        for key in self.unmapped_fields:
            tag_count = {}
            max_count = 0
            max_tag = None
            for value in self.covered_by[key]:
                for sql_tag in self.covered_by[key][value]:
                    if sql_tag not in tag_count:
                        tag_count[sql_tag] = 0
                    tag_count[sql_tag] += 1
                    if tag_count[sql_tag] > max_count:
                        max_count = tag_count[sql_tag]
                        max_tag = sql_tag
            if max_count > 0 and max_count >= (0.9 * len(self.values[key])):
                finished.append(tuple([key, max_tag]))
        for key, tag in finished:
            self.unmapped_fields.remove(key)
            self.mapped_fields.add(key)
            self.mapping[key] = tag

    def all_fields_mapped(self):
        return len(self.unmapped_fields) == 0
        
class PrecomputedTables:

    def __init__(self, dir_name):
        #print(dir_name)
        self.all_tables = []
        self.unmapped_tables = {}
        self.mapped_tables = {}
        self.sql_primary_key = {}
        self.sql_tables = None
        self.preloaded_mapping = False
        os.chdir(dir_name)
        # This is to output a tsv given an original mappings file (generated by sql_reader)
        #if os.path.exists(f"{dir_name}/mapping.txt"):
        #    with open(f"{dir_name}/mapping.txt", "r") as f:
        #        for line in f:
        #            line = line.strip("\n")
        #            if not line.startswith("\t"):
        #                fname = line
        #            else:
        #                line = line.strip("\t")
        #                n = line.find(" ->")
        #                pre = line[:n]
        #                pos = line[n+4:]
        #                if pos == "???":
        #                    continue
        #                table, field = tuple(pos.split())
        #                print("\t".join([fname, pre, table, field]))
        for file_name in glob.glob("ncRNA_genes_*.json"):
            with open(file_name) as f:
                json_dict = json.load(f)
            self._process_ncrna(json_dict)
        for file_name in glob.glob("*.tsv"):
            table = Table(file_name)
            self.unmapped_tables[file_name] = table
            self.all_tables.append(table)
            self._process_tsv(file_name)
        if os.path.exists(f"{dir_name}/mapping.txt"):
            self.preloaded_mapping = True
            mappings = {}
            with open(f"{dir_name}/mapping.txt", "r") as f:
                for line in f:
                    line = line.strip("\n").split("\t")
                    fname, column, referenced_table, referenced_column = tuple(line)
                    if fname not in mappings:
                        mappings[fname] = []
                    mappings[fname].append(tuple([column, referenced_table, referenced_column]))
            finished = []
            for key, table in self.unmapped_tables.items():
                if key not in mappings:
                    continue
                for column, referenced_table, referenced_colum in mappings[key]:
                    table.unmapped_fields.remove(column)
                    table.mapped_fields.add(column)
                    table.mapping[column] = tuple([referenced_table, referenced_column])
                if table.all_fields_mapped():
                    finished.append(key)
            for key in finished:
                self.mapped_tables[key] = self.unmapped_tables.pop(key)

    def mappings_str(self):
        output = []
        output.append(f"Fully mapped tables: {len(self.mapped_tables)}\n")
        for key, table in self.mapped_tables.items():
            output.append(f"{key}")
            for key in table.mapped_fields:
                sql_table, sql_field = table.mapping[key]
                output.append(f"\t{key} -> {sql_table} {sql_field}")
        if len(self.unmapped_tables) == 0:
            return "\n".join(output)
        output.append(f"Non (or partially) mapped tables: {len(self.unmapped_tables)}\n")
        for key, table in self.unmapped_tables.items():
            output.append(f"{key}")
            for key in table.mapped_fields:
                sql_table, sql_field = table.mapping[key]
                output.append(f"\t{key} -> {sql_table} {sql_field}")
            for key in table.unmapped_fields:
                output.append(f"\t{key} -> ???")
        return "\n".join(output) + "\n"

    def _add_row(self, file_name, row):
        self.unmapped_tables[file_name].add_row(row)

    def _set_header(self, file_name, header):
        self.unmapped_tables[file_name].set_header(header)

    def _process_tsv(self, file_name):
        header = None
        with open(file_name) as f:
            rows = csv.reader(f, delimiter="\t", quotechar='"')
            for row in rows:
                if not row:
                    continue
                if not row[0].startswith("#"):
                    if header is None:
                        header = [previous[0].lstrip("#"), *previous[1:]]
                        self._set_header(file_name, header)
                        #print(header)
                    self._add_row(file_name, row)
                    #print(row)
                if not row[0].startswith("#-----"):
                    previous = row
        #self.unmapped_tables[file_name].print_values()

    def _process_ncrna(self, json_dict):
        known_keys = [
            "primaryId",
            "symbol",
            "sequence",
            "taxonId",
            "soTermId",
            "gene",
            "symbolSynonyms",
            "publications",
            "genomeLocations",
            "url",
            "crossReferenceIds",
            "relatedSequences",
        ]
        main_table_header = [
            "primaryId",
            "symbol",
            "sequence",
            "taxonId",
            "soTermId",
            "gene_geneId",
            "gene_symbol",
            "gene_locusTag"
        ]
        main_table_rows = []
        synonyms_table_header = ["symbol1", "symbol2"]
        synomyms_table_rows = []
        cross_reference_table_header = ["symbol1", "symbol2"]
        cross_reference_table_rows = []
        related_sequences_table_header = ["primaryId", "sequenceId", "relationship"]
        related_sequences_table_rows = []
        gene_synonyms_table_header = ["symbol1", "symbol2"]
        gene_synomyms_table_rows = []
        publications_table_header = ["primaryId", "publication"]
        publications_table_rows = []
        genome_locations_table_header = [
            "primaryId", 
            "assembly", 
            "gca_accession", 
            "INSDC_accession", 
            "chromosome", 
            "strand", 
            "startPosition", 
            "endPosition"
        ]
        genome_locations_table_rows = []
        for row in json_dict["data"]:
            for key in row:
                assert key in known_keys, f"Invalid key: {key}"
                    
            #fbid = row["primaryId"].split(":")[1]
            fbid = row["primaryId"]
            symbol = row["symbol"]
            sequence = row["sequence"]
            taxonid = row["taxonId"]
            sotermid = row["soTermId"]
            gene_geneid = row["gene"]["geneId"]
            gene_symbol = row["gene"]["symbol"]
            gene_locustag = row["gene"]["locusTag"]
            main_table_rows.append([
                fbid, symbol, sequence, taxonid, sotermid, 
                gene_geneid, gene_symbol, gene_locustag])
            if "symbolSynonyms" in row:
                for synonym in row["symbolSynonyms"]:
                    synomyms_table_rows.append([symbol, synonym])
                    synomyms_table_rows.append([synonym, symbol])
            if "crossReferenceIds" in row:
                for cross_reference in row["crossReferenceIds"]:
                    cross_reference_table_rows.append([symbol, cross_reference])
                    cross_reference_table_rows.append([cross_reference, symbol])
            if "relatedSequences" in row:
                for related_sequence in row["relatedSequences"]:
                    related_sequences_table_rows.append([
                        fbid, 
                        related_sequence["sequenceId"], 
                        related_sequence["relationship"]])
            if "synonyms" in row["gene"]:
                for synonym in row["gene"]["synonyms"]:
                    gene_synomyms_table_rows.append([gene_symbol, synonym])
                    gene_synomyms_table_rows.append([synonym, gene_symbol])
            if "publications" in row:
                for publication in row["publications"]:
                    publications_table_rows.append([fbid, publication])
            for genome_location in row["genomeLocations"]:
                for exon in genome_location["exons"]:
                    genome_locations_table_rows.append([
                        fbid,
                        genome_location["assembly"],
                        genome_location["gca_accession"],
                        exon["INSDC_accession"],
                        exon["chromosome"],
                        exon["strand"],
                        str(exon["startPosition"]),
                        str(exon["endPosition"])])
        table_list = [
            ("ncRNA_genes", main_table_header, main_table_rows),
            ("ncRNA_genes_synonyms", synonyms_table_header, synomyms_table_rows),
            ("ncRNA_genes_cross_references", cross_reference_table_header, cross_reference_table_rows),
            ("ncRNA_genes_related_sequences", related_sequences_table_header, related_sequences_table_rows),
            ("ncRNA_genes_gene_synonyms", gene_synonyms_table_header, gene_synomyms_table_rows),
            ("ncRNA_genes_publications", publications_table_header, publications_table_rows),
            ("ncRNA_genes_genome_locations", genome_locations_table_header, genome_locations_table_rows)
        ]
        for table_name, header, rows in table_list:
            table = Table(table_name)
            table.set_header(header)
            for row in rows:
                table.add_row(row)
            self.unmapped_tables[table_name] = table
            self.all_tables.append(table)

    def set_sql_primary_key(self, sql_table, field):
        self.sql_primary_key[sql_table] = field

    def all_tables_mapped(self):
        return self.preloaded_mapping or len(self.unmapped_tables) == 0

    def check_field_value(self, sql_table, sql_field, value):
        finished = []
        for key, table in self.unmapped_tables.items():
            table.check_field_value(sql_table, sql_field, value)
            if table.all_fields_mapped():
                finished.append(key)
        for key in finished:
            self.mapped_tables[key] = self.unmapped_tables.pop(key)

    def get_relevant_sql_tables(self):
        answer = set()
        for table in self.all_tables:
            answer = answer.union(table.get_relevant_sql_tables())
        return answer

    def check_nearly_matched_tables(self):
        finished = []
        for key, table in self.unmapped_tables.items():
            table.check_near_match()
            if table.all_fields_mapped():
                finished.append(key)
        for key in finished:
            self.mapped_tables[key] = self.unmapped_tables.pop(key)

