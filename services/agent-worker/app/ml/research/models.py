"""Model registry for the bake-off — many estimators behind one interface.

Every entry is a scikit-learn-compatible classifier (``fit``/``predict`` and,
where possible, ``predict_proba``). Because the API is uniform, the evaluation
harness loops over them blindly. Adding a model is one line here.

Optional heavyweights (XGBoost/LightGBM/CatBoost) are added only if importable,
so the harness runs with a stock scikit-learn install and gracefully gains models
when the extras are present. Each estimator is wrapped in a Pipeline that imputes
missing values and (for distance/linear models) standardizes — small-data hygiene
so one model family isn't unfairly handicapped.
"""

from __future__ import annotations

from typing import Any

from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
    StackingClassifier,
    VotingClassifier,
)
from sklearn.gaussian_process import GaussianProcessClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

# Models that need standardized, imputed input (distances/kernels/linear).
_NEEDS_SCALING = ("knn", "svm_rbf", "gaussian_process", "logistic", "ridge", "lda")


def _scaled(estimator: Any) -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", estimator),
        ]
    )


def _imputed(estimator: Any) -> Pipeline:
    return Pipeline(
        [
            # Trees handle unscaled features fine but still need finite inputs.
            ("impute", SimpleImputer(strategy="median")),
            ("model", estimator),
        ]
    )


def _base_classifiers(seed: int) -> dict[str, Any]:
    return {
        # --- baselines (must be beaten to claim any skill) ---
        "baseline_majority": _imputed(DummyClassifier(strategy="most_frequent")),
        "baseline_stratified": _imputed(
            DummyClassifier(strategy="stratified", random_state=seed)
        ),
        # --- linear / probabilistic ---
        "logistic": _scaled(LogisticRegression(max_iter=1000)),
        "ridge": _scaled(RidgeClassifier()),
        "lda": _scaled(LinearDiscriminantAnalysis()),
        "naive_bayes": _imputed(GaussianNB()),
        # --- trees / ensembles (tabular workhorses) ---
        "decision_tree": _imputed(DecisionTreeClassifier(random_state=seed)),
        "random_forest": _imputed(
            RandomForestClassifier(n_estimators=300, random_state=seed, n_jobs=-1)
        ),
        "extra_trees": _imputed(
            ExtraTreesClassifier(n_estimators=300, random_state=seed, n_jobs=-1)
        ),
        "grad_boost": _imputed(GradientBoostingClassifier(random_state=seed)),
        "hist_grad_boost": _imputed(
            HistGradientBoostingClassifier(random_state=seed)
        ),
        # --- distance / kernel ---
        "knn": _scaled(KNeighborsClassifier(n_neighbors=15)),
        # No probability=True (deprecated + slow); the harness ranks via the SVM's
        # decision_function instead, which is enough for IC/ROC-AUC.
        "svm_rbf": _scaled(SVC(kernel="rbf", random_state=seed)),
        "gaussian_process": _scaled(GaussianProcessClassifier(random_state=seed)),
    }


def _optional_boosters(seed: int) -> dict[str, Any]:
    """XGBoost/LightGBM/CatBoost classifiers when their libs are installed."""
    extra: dict[str, Any] = {}
    try:
        from xgboost import XGBClassifier

        extra["xgboost"] = _imputed(
            XGBClassifier(
                n_estimators=300,
                eval_metric="logloss",
                random_state=seed,
                tree_method="hist",
            )
        )
    except ImportError:
        pass
    try:
        from lightgbm import LGBMClassifier

        extra["lightgbm"] = _imputed(
            LGBMClassifier(n_estimators=300, random_state=seed, verbose=-1)
        )
    except ImportError:
        pass
    try:
        from catboost import CatBoostClassifier

        extra["catboost"] = _imputed(
            CatBoostClassifier(
                iterations=300, random_seed=seed, verbose=False, allow_writing_files=False
            )
        )
    except ImportError:
        pass
    return extra


def _meta_ensembles(seed: int) -> dict[str, Any]:
    """Voting/Stacking over a few strong, diverse base learners."""
    estimators = [
        ("logistic", _scaled(LogisticRegression(max_iter=1000))),
        ("random_forest", _imputed(RandomForestClassifier(n_estimators=200, random_state=seed))),
        ("hist_grad_boost", _imputed(HistGradientBoostingClassifier(random_state=seed))),
    ]
    return {
        "voting_soft": VotingClassifier(estimators=estimators, voting="soft"),
        "stacking": StackingClassifier(
            estimators=estimators,
            final_estimator=LogisticRegression(max_iter=1000),
            cv=3,
        ),
    }


def build_classifier_registry(seed: int = 42) -> dict[str, Any]:
    """All bake-off classifiers, keyed by name and ready to ``fit``.

    Includes baselines first (for honest comparison), then the model families, any
    installed optional boosters, and meta-ensembles.
    """
    registry = _base_classifiers(seed)
    registry.update(_optional_boosters(seed))
    registry.update(_meta_ensembles(seed))
    return registry
