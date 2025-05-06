import time
import random
from simpleprogress import Progress
from datetime import datetime
from typing import List, Dict, Any


class DummyDataset:
    def __init__(self, name: str, num_docs: int, num_examples: int):
        self.name = name
        self.documents = [f"doc_{i}" for i in range(num_docs)]
        self.examples = [f"example_{i}" for i in range(num_examples)]


class DummyPipeline:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ingested_docs = set()

    def ingest(self, doc: str):
        time.sleep(0.1)
        self.ingested_docs.add(doc)

    def retrieve(self, query: str) -> str:
        time.sleep(0.2)
        return f"result_for_{query}"


def calculate_accuracy(output: str, expected: str) -> float:
    time.sleep(0.05)
    return random.uniform(0.7, 0.95)


def process_example(example: str, pipeline: DummyPipeline) -> float:
    output = pipeline.retrieve(example)
    return calculate_accuracy(output, example)


def ingest(pipeline: DummyPipeline, documents: List[str], progress):
    import concurrent.futures

    def ingest_doc(doc):
        pipeline.ingest(doc)
        progress.update()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(ingest_doc, documents)


def process_examples(pipeline: DummyPipeline, examples: List[str], progress) -> float:
    accuracies = []
    for example in examples:
        accuracy = process_example(example, pipeline)
        accuracies.append(accuracy)
        progress.update()
    return sum(accuracies) / len(accuracies)


def run_experiment(dataset: DummyDataset, config: Dict[str, Any], progress) -> float:
    with progress.child(f"experiment {dataset.name} - {config['name']}") as exp:
        pipeline = DummyPipeline(config)

        with exp.child("ingestion", total=len(dataset.documents)) as ingest_progress:
            ingest(pipeline, dataset.documents, ingest_progress)

        with exp.child("evaluation", total=len(dataset.examples)) as eval_progress:
            avg_accuracy = process_examples(pipeline, dataset.examples, eval_progress)

        return avg_accuracy


def main():
    # Setup dummy data
    datasets = [
        DummyDataset("large", num_docs=200, num_examples=50),
        DummyDataset("small", num_docs=50, num_examples=30),
    ]

    configs = [
        {"name": "config_a", "param1": 1, "param2": "value1"},
        {"name": "config_b", "param1": 2, "param2": "value2"},
    ]

    # Setup progress tracking
    prg_path = f"logs/run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.progress.jsonl"
    prg = Progress.open(prg_path)

    results = {}
    with prg.task("experiments", total=len(datasets) * len(configs)) as exps:
        for dataset in datasets:
            for config in configs:
                avg_accuracy = run_experiment(dataset, config, exps)
                results[(dataset.name, config["name"])] = avg_accuracy
                exps.update()

    # Print results
    print("\nResults:")
    for (dataset, config), accuracy in results.items():
        print(f"{dataset} - {config}: {accuracy:.2%}")


if __name__ == "__main__":
    main()
