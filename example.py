import concurrent.futures
import time
from datetime import datetime
from typing import Any, Dict, List

from simpleprogress import Progress


class Dataset:
    def __init__(self, name: str, num_docs: int, num_examples: int):
        self.name = name
        self.documents = [f"doc_{i}" for i in range(num_docs)]
        self.examples = [f"example_{i}" for i in range(num_examples)]


class Pipeline:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ingested_docs = set()

    def ingest(self, doc: str):
        time.sleep(0.2)
        self.ingested_docs.add(doc)

    def retrieve(self, query: str) -> str:
        time.sleep(0.1)
        return f"result_for_{query}"


def ingest(pipeline: Pipeline, documents: List[str], progress):
    def ingest_doc(doc):
        pipeline.ingest(doc)
        progress.update()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(ingest_doc, documents)


def process_examples(pipeline: Pipeline, examples: List[str], progress) -> List[str]:
    results = []
    for example in examples:
        result = pipeline.retrieve(example)
        results.append(result)
        progress.update()
    return results


def run_experiment(dataset: Dataset, config: Dict[str, Any], progress) -> float:
    with progress.child(f"experiment_{dataset.name}_{config['name']}") as exp:
        pipeline = Pipeline(config)

        with exp.child("ingestion", total=len(dataset.documents)) as ingest_progress:
            ingest(pipeline, dataset.documents, ingest_progress)

        with exp.child("evaluation", total=len(dataset.examples)) as eval_progress:
            results = process_examples(pipeline, dataset.examples, eval_progress)

        return results


def run_grid(datasets: List[Dataset], configs: List[Dict[str, Any]]):
    prg_path = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.progress.jsonl"
    prg = Progress.open(prg_path)

    results = {}
    with prg.task("experiments", total=len(datasets) * len(configs)) as exps:
        for dataset in datasets:
            for config in configs:
                result = run_experiment(dataset, config, exps)
                results[(dataset.name, config["name"])] = result
                exps.update()
    return results


def main():
    datasets = [
        Dataset("large_ds", num_docs=200, num_examples=50),
        Dataset("small_ds", num_docs=50, num_examples=30),
    ]

    configs = [
        {"name": "config_a", "param1": 1, "param2": "value1"},
        {"name": "config_b", "param1": 2, "param2": "value2"},
    ]

    run_grid(datasets, configs)

    print("Done")


if __name__ == "__main__":
    main()
