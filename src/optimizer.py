import numpy as np
from scipy.optimize import minimize
from typing import List, Dict, Any

class CropOptimizer:
    def __init__(self, min_share: float = 0.05, max_share: float = 0.50):
        self.min_share = min_share
        self.max_share = max_share

    def _objective(self, allocation: np.ndarray, profits: np.ndarray) -> float:
        return -np.sum(allocation * profits)

    def optimize(self, user_area: float, crops: List[str], predicted_profits: List[float]) -> Dict[str, Any]:
        n = len(crops)
        profits = np.array(predicted_profits)
        x0 = np.array([user_area / n] * n)
        constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - user_area}]
        bounds = [(self.min_share * user_area, self.max_share * user_area) for _ in range(n)]

        result = minimize(self._objective, x0, args=(profits,), method="SLSQP", bounds=bounds, constraints=constraints)

        if not result.success:
            # Fallback to weighted distribution
            weights = profits / np.sum(profits)
            allocations = weights * user_area
            return {"success": True, "allocations": dict(zip(crops, np.round(allocations, 2))), "status": "fallback"}

        return {
            "success": True, 
            "allocations": dict(zip(crops, np.round(result.x, 2))),
            "total_profit": round(-result.fun, 2),
            "status": "optimized"
        }