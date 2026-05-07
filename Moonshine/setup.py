from setuptools import setup


PACKAGES = [
    "moonshine",
    "moonshine.agents",
    "moonshine.agent_runtime",
    "moonshine.gateway",
    "moonshine.moonshine_cli",
    "moonshine.skills",
    "moonshine.storage",
    "moonshine.tests",
    "moonshine.tools",
]


setup(
    name="moonshine",
    version="0.1.0",
    description="Linux-first math research agent harness with traceable storage",
    python_requires=">=3.8",
    packages=PACKAGES,
    package_dir={"moonshine": "."},
    include_package_data=True,
    package_data={
        "moonshine": [
            "README.md",
            "assets/**/*",
            "memory/**/*",
            "projects/**/*",
        ]
    },
    install_requires=[
        "tiktoken>=0.7.0",
        "lancedb>=0.14.0",
        "chromadb>=0.5.0",
        "langgraph>=0.2.0",
    ],
    extras_require={
        "tokenizer": ["tiktoken>=0.7.0"],
        "lancedb": ["lancedb>=0.14.0"],
        "chromadb": ["chromadb>=0.5.0"],
        "vector": ["lancedb>=0.14.0", "chromadb>=0.5.0"],
        "workflow": ["langgraph>=0.2.0"],
        "all": ["tiktoken>=0.7.0", "lancedb>=0.14.0", "chromadb>=0.5.0", "langgraph>=0.2.0"],
    },
    entry_points={
        "console_scripts": [
            "moonshine=moonshine.cli:main",
        ]
    },
)
