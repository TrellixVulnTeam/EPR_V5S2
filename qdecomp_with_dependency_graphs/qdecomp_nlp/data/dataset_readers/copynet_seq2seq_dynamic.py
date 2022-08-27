"""
based on allennlp_models/generation/dataset_readers/copynet_seq2seq.py
tag: v1.1.0
"""
import logging
from typing import List, Dict
import warnings

import numpy as np

from ast import literal_eval
from overrides import overrides

from allennlp.common.util import START_SYMBOL, END_SYMBOL
from allennlp.data.dataset_readers.dataset_reader import DatasetReader
from allennlp.data.fields import TextField, ArrayField, MetadataField, NamespaceSwappingField
from allennlp.data.instance import Instance
from allennlp.data.tokenizers import (
    Token,
    Tokenizer,
    SpacyTokenizer,
    PretrainedTransformerTokenizer,
)
from allennlp.data.token_indexers import TokenIndexer, SingleIdTokenIndexer

from qdecomp_nlp.data.dataset_readers.util import read_break_data

logger = logging.getLogger(__name__)


@DatasetReader.register("break_copynet_seq2seq")
class CopyNetDynamicDatasetReader(DatasetReader):
    """
    Read a tsv file containing paired sequences, and create a dataset suitable for a
    `CopyNet` model, or any model with a matching API.
    The expected format for each input line is: <source_sequence_string><tab><target_sequence_string>.
    An instance produced by `CopyNetDatasetReader` will containing at least the following fields:
    - `source_tokens`: a `TextField` containing the tokenized source sentence.
       This will result in a tensor of shape `(batch_size, source_length)`.
    - `source_token_ids`: an `ArrayField` of size `(batch_size, source_length)`
      that contains an ID for each token in the source sentence. Tokens that
      match at the lowercase level will share the same ID. If `target_tokens`
      is passed as well, these IDs will also correspond to the `target_token_ids`
      field, i.e. any tokens that match at the lowercase level in both
      the source and target sentences will share the same ID. Note that these IDs
      have no correlation with the token indices from the corresponding
      vocabulary namespaces.
    - `source_to_target`: a `NamespaceSwappingField` that keeps track of the index
      of the target token that matches each token in the source sentence.
      When there is no matching target token, the OOV index is used.
      This will result in a tensor of shape `(batch_size, source_length)`.
    - `metadata`: a `MetadataField` which contains the source tokens and
      potentially target tokens as lists of strings.
    When `target_string` is passed, the instance will also contain these fields:
    - `target_tokens`: a `TextField` containing the tokenized target sentence,
      including the `START_SYMBOL` and `END_SYMBOL`. This will result in
      a tensor of shape `(batch_size, target_length)`.
    - `target_token_ids`: an `ArrayField` of size `(batch_size, target_length)`.
      This is calculated in the same way as `source_token_ids`.
    See the "Notes" section below for a description of how these fields are used.
    # Parameters
    target_namespace : `str`, required
        The vocab namespace for the targets. This needs to be passed to the dataset reader
        in order to construct the NamespaceSwappingField.
    source_tokenizer : `Tokenizer`, optional
        Tokenizer to use to split the input sequences into words or other kinds of tokens. Defaults
        to `SpacyTokenizer()`.
    target_tokenizer : `Tokenizer`, optional
        Tokenizer to use to split the output sequences (during training) into words or other kinds
        of tokens. Defaults to `source_tokenizer`.
    source_token_indexers : `Dict[str, TokenIndexer]`, optional
        Indexers used to define input (source side) token representations. Defaults to
        `{"tokens": SingleIdTokenIndexer()}`.
    # Notes
    In regards to the fields in an `Instance` produced by this dataset reader,
    `source_token_ids` and `target_token_ids` are primarily used during training
    to determine whether a target token is copied from a source token (or multiple matching
    source tokens), while `source_to_target` is primarily used during prediction
    to combine the copy scores of source tokens with the generation scores for matching
    tokens in the target namespace.
    """

    def __init__(self,
                 target_namespace: str,
                 source_tokenizer: Tokenizer = None,
                 target_tokenizer: Tokenizer = None,
                 source_token_indexers: Dict[str, TokenIndexer] = None,
                 delimiter: str = ",",
                 separator_symbol: str = "@@SEP@@",
                 copy_token: str = "@COPY@",
                 dynamic_vocab: bool = False,
                 **kwargs,
                 ) -> None:
        super().__init__(**kwargs)
        self._target_namespace = target_namespace
        self._source_tokenizer = source_tokenizer or SpacyTokenizer()
        self._target_tokenizer = target_tokenizer or self._source_tokenizer
        self._allowed_tokenizer = self._source_tokenizer
        self._source_token_indexers = source_token_indexers or {"tokens": SingleIdTokenIndexer()}
        self._target_token_indexers: Dict[str, TokenIndexer] = {
                "tokens": SingleIdTokenIndexer(namespace=self._target_namespace)
        }

        if (
            isinstance(self._target_tokenizer, PretrainedTransformerTokenizer)
            and self._target_tokenizer._add_special_tokens
        ):
            warnings.warn(
                "'add_special_tokens' is True for target_tokenizer, which is a PretrainedTransformerTokenizer. "
                "This means special tokens, such as '[CLS]' and '[SEP]', will probably end up in "
                "your model's predicted target sequences. "
                "If this is not what you intended, make sure to specify 'add_special_tokens: False' for "
                "your target_tokenizer.",
                UserWarning,
            )

        self._delimiter = delimiter
        self._separator_symbol = separator_symbol
        self._copy_token = copy_token
        self._dynamic_vocab = dynamic_vocab

    @overrides
    def _read(self, file_path):
        logger.info("Reading instances from lines in file at: %s", file_path)
        args = ['question_text', 'decomposition', 'lexicon_tokens'] if self._dynamic_vocab \
            else ['question_text', 'decomposition']
        for instance in read_break_data(file_path, self._delimiter, self.text_to_instance, args):
            yield instance

    @staticmethod
    def _tokens_to_ids(tokens: List[Token]) -> List[int]:
        ids: Dict[str, int] = {}
        out: List[int] = []
        for token in tokens:
            out.append(ids.setdefault(token.text, len(ids)))
        return out

    @overrides
    def text_to_instance(self, source_string: str, target_string: str = None, allowed_string: str = None) -> Instance:
        """
        Turn raw source string and target string into an `Instance`.
        # Parameters
        source_string : `str`, required
        target_string : `str`, optional (default = `None`)
        # Returns
        `Instance`
            See the above for a description of the fields that the instance will contain.
        """

        tokenized_source = self._source_tokenizer.tokenize(source_string)
        if not tokenized_source:
            # If the tokenized source is empty, it will cause issues downstream.
            raise ValueError(f"source tokenizer produced no tokens from source '{source_string}'")
        source_field = TextField(tokenized_source, self._source_token_indexers)

        # For each token in the source sentence, we keep track of the matching token
        # in the target sentence (which will be the OOV symbol if there is no match).
        source_to_target_field = NamespaceSwappingField(tokenized_source, self._target_namespace)

        meta_fields = {"source_tokens": [x.text for x in tokenized_source]}
        fields_dict = {"source_tokens": source_field, "source_to_target": source_to_target_field}

        if target_string is not None:
            tokenized_target = self._target_tokenizer.tokenize(target_string)
            tokenized_target.insert(0, Token(START_SYMBOL))
            tokenized_target.append(Token(END_SYMBOL))
            target_field = TextField(tokenized_target, self._target_token_indexers)

            fields_dict["target_tokens"] = target_field
            meta_fields["target_tokens"] = [y.text for y in tokenized_target[1:-1]]
            source_and_target_token_ids = self._tokens_to_ids(tokenized_source + tokenized_target)
            source_token_ids = source_and_target_token_ids[: len(tokenized_source)]
            fields_dict["source_token_ids"] = ArrayField(np.array(source_token_ids))
            target_token_ids = source_and_target_token_ids[len(tokenized_source) :]
            fields_dict["target_token_ids"] = ArrayField(np.array(target_token_ids))
        else:
            source_token_ids = self._tokens_to_ids(tokenized_source)
            fields_dict["source_token_ids"] = ArrayField(np.array(source_token_ids))

        # allowed tokens
        if allowed_string:
            source_tokens_text = meta_fields["source_tokens"]
            target_tokens_text = meta_fields.get("target_tokens", [])
            parsed_allowed_string = self._parse_allowed_string(allowed_string, source_tokens_text, target_tokens_text)
            tokenized_allowed = self._allowed_tokenizer.tokenize(parsed_allowed_string)
            tokenized_allowed.insert(0, Token(self._copy_token))
            tokenized_allowed.insert(0, Token(self._separator_symbol))
            tokenized_allowed.insert(0, Token(END_SYMBOL))
            tokenized_allowed.insert(0, Token(START_SYMBOL))
            allowed_field = TextField(tokenized_allowed, self._source_token_indexers)
            allowed_token_ids = NamespaceSwappingField(tokenized_allowed, self._target_namespace)
            meta_fields["allowed_tokens"]= [x.text for x in tokenized_allowed]
            fields_dict.update({ "allowed_tokens": allowed_field, "allowed_token_ids": allowed_token_ids})

        fields_dict["metadata"] = MetadataField(meta_fields)

        return Instance(fields_dict)

    @staticmethod
    def _parse_allowed_string(allowed_string: str,
                              source_tokens_text: List[str],
                              target_tokens_text: List[str]) -> str:
        allowed_tokens = [t.strip() for t in literal_eval(allowed_string) if type(t) == str]
        allowed_tokens = list(set(
            ' '.join(allowed_tokens).split(' ') + source_tokens_text + target_tokens_text
        ))
        return ' '.join(allowed_tokens)

