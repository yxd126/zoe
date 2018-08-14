import json
import math
import os
import pickle

import numpy as np
import tensorflow as tf

from bilm import dump_bilm_embeddings_inner, dump_bilm_embeddings


class ElmoProcessor:

    RANKED_RETURN_NUM = 20

    def __init__(self):
        self.datadir = os.path.join('bilm-tf', 'model')
        self.vocab_file = os.path.join(self.datadir, 'vocab_test.txt')
        self.options_file = os.path.join(self.datadir, 'options.json')
        self.weight_file = os.path.join(self.datadir, 'elmo_2x4096_512_2048cnn_2xhighway_5.5B_weights.hdf5')
        with open('data/sent_example.pickle', 'rb') as handle:
            self.sent_example_map = pickle.load(handle)
        self.target_embedding_map = {}
        self.wikilinks_embedding_map = {}
        self.computed_elmo_map = {}

    def process_batch(self, sentences):
        tokenized_context = [sentence.split() for sentence in sentences]
        freq_map = {}

        for i in range(0, len(tokenized_context)):
            for j in range(0, len(tokenized_context[i])):
                key = tokenized_context[i][j]
                if key in freq_map:
                    freq_map[key] = freq_map[key] + 1.0
                else:
                    freq_map[key] = 1.0

        embedding_map = dump_bilm_embeddings(
            self.vocab_file, sentences, self.options_file, self.weight_file
        )

        ret_map = {}
        for sent_id in range(0, len(sentences)):
            sent_embedding = embedding_map[sent_id]
            for i in range(0, len(tokenized_context[sent_id])):
                key = tokenized_context[sent_id][i]
                concat = np.concatenate([
                    sent_embedding[0][i],
                    sent_embedding[1][i],
                    sent_embedding[2][i]
                ])
                if key in ret_map:
                    ret_map[key] = ret_map[key] + concat
                else:
                    ret_map[key] = concat
                assert(len(ret_map[key]) == 3 * 1024)
        ret_map_avg = {}
        for key in ret_map:
            dividend = freq_map[key]
            ret_map_avg[key] = list(ret_map[key] / dividend)
        tf.reset_default_graph()
        return ret_map_avg

    def process_single(self, sentence):
        tokens = sentence.split()
        embedding = dump_bilm_embeddings_inner(
            self.vocab_file, sentence, self.options_file, self.weight_file
        )
        assert(len(embedding[0]) == len(tokens))
        ret_map = {}
        for i in range(0, len(tokens)):
            ret_map[tokens[i]] = list(embedding[0][i]) + list(embedding[1][i]) + list(embedding[2][i])
            assert(len(ret_map[tokens[i]]) == 3 * 1024)
        tf.reset_default_graph()
        return ret_map

    @staticmethod
    def cosine(vec_a, vec_b):
        assert(len(vec_a) == len(vec_b))
        square_a = 0.0
        square_b = 0.0
        score_mul = 0.0
        for i in range(0, len(vec_a)):
            square_a += float(vec_a[i]) * float(vec_a[i])
            square_b += float(vec_b[i]) * float(vec_b[i])
            score_mul += float(vec_a[i]) * float(vec_b[i])
        return score_mul / math.sqrt(square_a * square_b)

    def load_cached_embeddings(self, target_file_name, wikilinks_file_name):
        with open(target_file_name, "rb") as handle:
            self.target_embedding_map = pickle.load(handle)
        with open(wikilinks_file_name, "rb") as handle:
            self.wikilinks_embedding_map = pickle.load(handle)

    def rank_candidates(self, sentence, candidates):
        sentences_to_process = []
        candidates = [x[0] for x in candidates]
        if sentence.get_mention_surface() not in self.target_embedding_map:
            sentences_to_process.append(sentence.get_sent_str())
        for candidate in candidates:
            if candidate not in self.sent_example_map:
                continue
            # skip if cached
            if candidate in self.wikilinks_embedding_map:
                continue
            example_sentences_str = self.sent_example_map[candidate]
            example_sentences = example_sentences_str.split("|||")
            for i in range(0, min(len(example_sentences), 10)):
                sentences_to_process.append(example_sentences[i])
        elmo_map = {}
        if len(sentences_to_process) > 0:
            elmo_map = self.process_batch(sentences_to_process)
            self.computed_elmo_map = elmo_map

        if sentence.get_mention_surface() in self.target_embedding_map:
            target_vec = self.target_embedding_map[sentence.get_mention_surface()]
        else:
            target_vec = elmo_map[sentence.get_mention_surface()]
        results = {}
        for candidate in candidates:
            wikilinks_vec = []
            if candidate in self.wikilinks_embedding_map:
                wikilinks_vec = self.wikilinks_embedding_map[candidate]
            if candidate in elmo_map:
                wikilinks_vec = elmo_map[candidate]
            if len(wikilinks_vec) > 0:
                results[candidate] = ElmoProcessor.cosine(target_vec, wikilinks_vec)
            else:
                results[candidate] = 0.0
        sorted_results = sorted(results.items(), key=lambda kv: kv[1], reverse=True)
        return [(x[0], x[1]) for x in sorted_results][:self.RANKED_RETURN_NUM]


class EsaProcessor:

    N_DOCUMENTS = 24504233.0
    RETURN_NUM = 300

    def __init__(self):
        with open('data/esa/esa.pickle', 'rb') as handle:
            self.esa_map = pickle.load(handle)
        with open('data/esa/freq.pickle', 'rb') as handle:
            self.freq_map = pickle.load(handle)
        with open('data/esa/invcount.pickle', 'rb') as handle:
            self.invcount_map = pickle.load(handle)

    @staticmethod
    def str2map(map_val):
        ret_map = {}
        entries = map_val.split("|")
        for entry in entries:
            key = entry.split("::")[0]
            val = entry.split("::")[1]
            ret_map[key] = float(val)
        return ret_map

    def get_candidates(self, sentence):
        tokens = sentence.tokens
        overall_map = {}
        doc_freq_map = {}
        max_acc = 0
        for token in tokens:
            if token in doc_freq_map:
                acc = doc_freq_map[token] + 1
            else:
                acc = 1
            if acc > max_acc:
                max_acc = acc
            doc_freq_map[token] = acc
        for token in tokens:
            if token in self.esa_map:
                idf_score = math.log(self.N_DOCUMENTS / float(self.freq_map[token]))
                tf_score = 0.5 + 0.5 * (float(doc_freq_map[token]) / float(max_acc))
                inv_freq = float(self.invcount_map[token])
                sub_map = EsaProcessor.str2map(self.esa_map[token])
                for key in sub_map:
                    weight = idf_score * tf_score * sub_map[key] / inv_freq
                    if key in overall_map:
                        overall_map[key] = overall_map[key] + weight
                    else:
                        overall_map[key] = weight
        sorted_overall_map = sorted(overall_map.items(), key=lambda kv: kv[1], reverse=True)
        return [(x[0], x[1]) for x in sorted_overall_map][:self.RETURN_NUM]


class InferenceProcessor:

    PROB_TRUST_THRESHOLD = 0.5
    ELMO_TO_ESA_MULTIPLIER = 15.0
    TRUST_CANDIDATE_SIZE = 10
    MIN_ELMO_SCORE_THRESHOLD = 0.65
    VOTING_THRESHOLD_NORMAL = 0.8
    VOTING_THRESHOLD_PRIOR = 0.3

    def __init__(self, mode):
        self.mode = mode
        self.mapping = {}
        mapping_file_name = "mapping/" + self.mode + ".mapping"
        with open(mapping_file_name) as f:
            for line in f:
                line = line.strip()
                self.mapping[line.split("\t")[0]] = line.split("\t")[1]
        with open("data/prior_prob.pickle", "rb") as handle:
            self.prior_prob_map = pickle.load(handle)
        with open("data/title2freebase.pickle", "rb") as handle:
            self.freebase_map = pickle.load(handle)

    #
    # process logic mappings from *.logic.mapping
    def get_final_types(self, current_set):
        logic_mapping_file_name = "mapping/" + self.mode + ".logic.mapping"
        if not os.path.isfile(logic_mapping_file_name):
            return current_set
        with open(logic_mapping_file_name) as f:
            for line in f:
                line = line.strip()
                line_group = line.split("\t")
                if line_group[0] == "+":
                    if line_group[1] in current_set:
                        current_set.add(line_group[2])
                else:
                    if line_group[1] in current_set and line_group[2] in current_set:
                        current_set.remove(line_group[2])
        return current_set

    def get_prob_title(self, surface):
        if surface in self.prior_prob_map:
            prior_prob = self.prior_prob_map[surface]
            if prior_prob[1] > self.PROB_TRUST_THRESHOLD:
                return prior_prob[0]
        return ""

    def get_mapped_types_of_title(self, title):
        if title not in self.freebase_map:
            return set()
        freebase_types = self.freebase_map[title].split(",")
        mapped_set = set()
        for t in freebase_types:
            converted_type = "/" + t.replace(".", "/")
            if converted_type in self.mapping:
                mapped_set.add(self.mapping[converted_type])
        return mapped_set

    #
    # Note: slightly different than Java version
    def get_coarse_types_of_title(self, title):
        fine_types = self.get_types_of_title(title)
        ret = set()
        for t in fine_types:
            ret.add("/" + t.split("/")[1])
        return ret

    def get_types_of_title(self, title):
        if title not in self.freebase_map:
            return []
        mapped_set = self.get_mapped_types_of_title(title)
        mapped_set_list = list(mapped_set)
        for t in mapped_set:
            if len(t.split("/")) >= 2:
                mapped_set_list.append("/" + t.split("/")[1])
        return self.get_final_types(set(mapped_set_list))

    def get_voted_coarse_type_of_title(self, title):
        mapped_set = self.get_mapped_types_of_title(title)
        coarse_freq = {}
        for t in mapped_set:
            key = "/" + t.split("/")[1]
            if key in coarse_freq:
                coarse_freq[key] = coarse_freq[key] + 1
            else:
                coarse_freq[key] = 1
        sorted_freq = sorted(coarse_freq.items(), key=lambda kv: kv[1], reverse=True)
        return sorted_freq[0][0]

    def compute_set_freq(self, titles):
        freq_map = {}
        for title in titles:
            title_types = self.get_types_of_title(title)
            for t in title_types:
                if t in freq_map:
                    freq_map[t] = freq_map[t] + 1
                else:
                    freq_map[t] = 1
        return freq_map

    def select_in_order(self, candidates, type_scores):
        for candidate in candidates:
            if len(self.get_mapped_types_of_title(candidate)) == 0:
                continue
            coarse_types = self.get_coarse_types_of_title(candidate)
            for ct in coarse_types:
                if ct in type_scores and type_scores[ct] > 1.0:
                    return candidate
        return candidates[0]

    def get_elmo_type_scores(self, candidates):
        ret_map = {}
        ret_map_freq = {}
        for candidate in candidates:
            title = candidate[0]
            score = candidate[1]
            for t in self.get_types_of_title(title):
                if t in ret_map:
                    ret_map[t] = ret_map[t] + score
                    ret_map_freq[t] = ret_map_freq[t] + 1.0
                else:
                    ret_map[t] = score
                    ret_map_freq[t] = 1.0
        for key in ret_map:
            ret_map[key] = ret_map[key] / ret_map_freq[key]
        return ret_map

    def get_inferred_types(self, selected, candidates, elmo_type_score, from_prior):
        if len(self.get_mapped_types_of_title(selected)) == 0:
            return []
        coarse_type = self.get_voted_coarse_type_of_title(selected)
        filtered_types = set()
        filtered_types.add(coarse_type)
        for t in self.get_mapped_types_of_title(selected):
            if t.startswith(coarse_type):
                filtered_types.add(t)
        freq_map = {}
        total = 0
        for candidate in candidates[:self.TRUST_CANDIDATE_SIZE]:
            if coarse_type in self.get_coarse_types_of_title(candidate):
                total += 1
                for t in self.get_types_of_title(candidate):
                    if t.startswith(coarse_type):
                        if t in freq_map:
                            freq_map[t] = freq_map[t] + 1
                        else:
                            freq_map[t] = 1
        selected_types = set()
        for key in freq_map:
            if key in elmo_type_score and elmo_type_score[key] > self.MIN_ELMO_SCORE_THRESHOLD:
                selected_types.add(key)
        consider_types = [x[0] for x in freq_map.items()]
        voting_threshold = self.VOTING_THRESHOLD_NORMAL
        if from_prior:
            consider_types = filtered_types
            voting_threshold = self.VOTING_THRESHOLD_PRIOR

        ret_types = set()
        ret_types.add(coarse_type)
        for t in consider_types:
            if t in freq_map:
                if float(freq_map[t]) > float(total) * voting_threshold:
                    ret_types.add(t)

        to_be_removed_types = set()
        for t in ret_types:
            if len(t.split("\t")) <= 2:
                continue
            for compare_type in freq_map:
                if compare_type.startswith(coarse_type) and compare_type not in ret_types and elmo_type_score[compare_type] > elmo_type_score[t]:
                    to_be_removed_types.add(t)
        final_ret_types = set()
        for t in ret_types:
            if t not in to_be_removed_types:
                final_ret_types.add(t)
        return final_ret_types

    #
    # sentence: a Sentence structure
    # elmo_candidates: (title, score) pairs
    # esa_candidates: (title, score) pairs
    def inference(self, sentence, elmo_candidates, esa_candidates):
        elmo_titles = [x[0] for x in elmo_candidates]
        esa_titles = [x[0] for x in esa_candidates]
        elmo_freq = self.compute_set_freq(elmo_titles)
        esa_freq = self.compute_set_freq(esa_titles)
        type_promotion_score_map = {}
        for t in elmo_freq:
            esa_freq_t = 0.0
            if t in esa_freq:
                esa_freq_t = float(esa_freq.get(t))
            type_promotion_score_map[t] = float(elmo_freq.get(t)) * self.ELMO_TO_ESA_MULTIPLIER / esa_freq_t

        selected_title = self.select_in_order(elmo_titles, type_promotion_score_map)

        prob_title = self.get_prob_title(sentence.get_mention_surface_raw())
        from_prior = False
        if prob_title != "" and len(self.get_mapped_types_of_title(prob_title)) > 0:
            prob_title_coarse_types = self.get_coarse_types_of_title(prob_title)
            for t in prob_title_coarse_types:
                if t in type_promotion_score_map and type_promotion_score_map[t] > 1.0:
                    selected_title = prob_title
                    from_prior = True
        # Now we have the most trust-worthy title
        elmo_type_score = self.get_elmo_type_scores(elmo_candidates)
        if selected_title in elmo_candidates:
            elmo_candidates.remove(selected_title)
        elmo_candidates.insert(0, selected_title)
        inferred_types = self.get_inferred_types(selected_title, elmo_candidates, elmo_type_score, from_prior)
        return self.get_final_types(set(inferred_types))


class Sentence:

    def __init__(self, tokens, mention_start, mention_end, gold_types):
        self.tokens = tokens
        self.mention_start = int(mention_start)
        self.mention_end = int(mention_end)
        self.gold_types = gold_types
        self.predicted_types = []

    def get_mention_surface(self):
        concat = ""
        for i in range(self.mention_start, self.mention_end):
            concat += self.tokens[i] + "_"
        if len(concat) > 0:
            concat = concat[:-1]
        return concat

    def get_mention_surface_raw(self):
        return self.get_mention_surface().replace("_", " ")

    def get_sent_str(self):
        concat = ""
        i = 0
        while i < len(self.tokens):
            if i == self.mention_start:
                concat += self.get_mention_surface()
                i = self.mention_end - 1
            else:
                concat += self.tokens[i]
            i += 1
            concat += " "
        if len(concat) > 0:
            concat = concat[:-1]
        return concat

    def set_predictions(self, predicted_types):
        self.predicted_types = predicted_types


class Evaluator:

    def __init__(self):
        self.sentences = []
        self.total_gold_types = 0
        self.total_predicted_types = 0
        self.total_matches = 0
        self.total_macro_precision = 0.0
        self.total_macro_recall = 0.0
        self.perfect_match = 0

    @staticmethod
    def compute_matches(set_a, set_b):
        count = 0
        for item in set_a:
            if item in set_b:
                count += 1
        return count

    @staticmethod
    def get_if_perfect_match(set_a, set_b):
        if len(set_a) == len(set_b):
            for item in set_a:
                if item not in set_b:
                    return False
            return True
        return False

    @staticmethod
    def compute_f1(precision, recall):
        if precision + recall == 0.0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    def print_performance(self, sentences):
        self.sentences = sentences
        for sentence in self.sentences:
            if len(sentence.gold_types) == 0:
                print("[ERROR]: encountered examples without correct answer.")
                return
            matches = self.compute_matches(sentence.gold_types, sentence.predicted_types)
            self.total_matches += matches
            self.total_gold_types += len(sentence.gold_types)
            self.total_predicted_types += len(sentence.predicted_types)
            if len(sentence.predicted_types) > 0:
                self.total_macro_precision += float(matches) / float(len(sentence.predicted_types))
            if len(sentence.gold_types) > 0:
                self.total_macro_recall += float(matches) / float(len(sentence.gold_types))
            if self.get_if_perfect_match(sentence.gold_types, sentence.predicted_types):
                self.perfect_match += 1
        strict_accuracy = 0.0
        if len(self.sentences) > 0:
            strict_accuracy = float(self.perfect_match) / float(len(self.sentences))

        micro_precision = 0.0
        if self.total_predicted_types > 0.0:
            micro_precision = float(self.total_matches) / float(self.total_predicted_types)
        micro_recall = 0.0
        if self.total_gold_types > 0.0:
            micro_recall = float(self.total_matches) / float(self.total_gold_types)
        micro_f1 = self.compute_f1(micro_precision, micro_recall)

        macro_precision = 0.0
        macro_recall = 0.0
        if len(self.sentences) > 0:
            macro_precision = float(self.total_macro_precision) / float(len(self.sentences))
            macro_recall = float(self.total_macro_recall) / float(len(self.sentences))
        macro_f1 = self.compute_f1(macro_precision, macro_recall)

        print("=========Performance==========")
        print("Strict Accuracy:\t" + str(strict_accuracy))
        print("---------------")
        print("Micro Precision:\t" + str(micro_precision))
        print("Micro Recall:\t" + str(micro_recall))
        print("Micro F1:\t" + str(micro_f1))
        print("---------------")
        print("Macro Precision:\t" + str(macro_precision))
        print("Macro Recall:\t" + str(macro_recall))
        print("Macro F1:\t" + str(macro_f1))


class DataReader:

    def __init__(self, data_file_name):
        self.sentences = []
        if not os.path.isfile(data_file_name):
            print("[ERROR] No sentences read.")
            return
        with open(data_file_name) as f:
            for line in f:
                line = line.strip()
                data = json.loads(line)
                tokens = data['tokens']
                mentions = data['mentions']
                for mention in mentions:
                    self.sentences.append(Sentence(tokens, mention['start'], mention['end'], mention['labels']))
