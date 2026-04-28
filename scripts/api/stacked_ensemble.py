import numpy as np

class StackedEnsemble:
    """Stacked ensemble meta-learner for combining base model predictions."""

    def __init__(self):
        self.meta_model = None
        self.model_names = []

    def predict_proba(self, base_predictions):
        meta_X = np.column_stack([base_predictions[name] for name in self.model_names])
        return self.meta_model.predict_proba(meta_X)[:, 1]
