from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from l5kit.cle.metric_set import L5MetricSet
from l5kit.environment.gym_metric_set import L5GymCLEMetricSet
from l5kit.simulation.unroll import SimulationOutputCLE


class Reward(ABC):
    """Base class interface for gym environment reward."""
    #: The prefix that will identify this reward class
    reward_prefix: str

    @abstractmethod
    def reset(self) -> None:
        """Reset the reward state when new episode starts.
        """
        raise NotImplementedError

    @abstractmethod
    def get_reward(self, frame_index: int, simulated_outputs: List[SimulationOutputCLE]) -> Dict[str, float]:
        """Return the reward at a particular time-step during the episode.

        :param frame_index: the frame index for which reward is to be calculated
        :param simulated_outputs: the object contain the ego target and prediction attributes
        :return: reward at a particular frame index (time-step) during the episode containing total reward
            and individual components that make up the reward.
        """
        raise NotImplementedError


class CLEReward(Reward):
    """This class is responsible for calculating reward during close loop simulation
    within the gym-compatible L5Kit environment.

    :param reward_prefix: the prefix that will identify this reward class
    :param metric_set: the set of metrics to compute
    :param enable_clip: flag to determine whether to clip reward
    :param rew_clip_thresh: the threshold to clip the reward
    :param use_yaw: flag to penalize the yaw prediction
    :param yaw_weight: weight of the yaw error
    """

    def __init__(self, reward_prefix: str = "CLE", metric_set: Optional[L5MetricSet] = None,
                 enable_clip: bool = True, rew_clip_thresh: float = 15.0,
                 use_yaw: Optional[bool] = True, yaw_weight: Optional[float] = 20.0) -> None:
        """Constructor method
        """
        self.reward_prefix = reward_prefix
        # Metric Set
        self.metric_set = metric_set if metric_set is not None else L5GymCLEMetricSet()

        self.use_yaw = use_yaw
        self.yaw_weight = yaw_weight

        self.enable_clip = enable_clip
        self.rew_clip_thresh = rew_clip_thresh

    def reset(self) -> None:
        """Reset the closed loop evaluator when a new episode starts.
        """
        self.metric_set.reset()

    def get_reward(self, frame_index: int, simulated_outputs: List[SimulationOutputCLE]) -> Dict[str, float]:
        """Get the reward for the given step in close loop training.

        :param frame_index: the frame index for which reward is to be calculated
        :param simulated_outputs: the object contain the ego target and prediction attributes
        :return: the dictionary containing total reward and individual components that make up the reward
        """
        scene_id = simulated_outputs[0].scene_id
        self.metric_set.evaluate(simulated_outputs)

        scene_metrics = self.metric_set.evaluator.scene_metric_results[scene_id]
        dist_error = scene_metrics['displacement_error_l2'][frame_index + 1]
        yaw_error = self.yaw_weight * scene_metrics['yaw_error_closest_angle'][frame_index + 1]

        # clip the distance error (in x, y) only, not the yaw error (yaw error is bounded).
        dist_reward = float(-dist_error.item())
        if self.enable_clip:
            dist_reward = max(-self.rew_clip_thresh, -dist_error.item())

        # use yaw
        yaw_reward = 0.0
        if self.use_yaw:
            yaw_reward -= yaw_error.item()

        # Total reward
        total_reward = dist_reward + yaw_reward

        reward_dict = {"total": total_reward, "distance": dist_reward, "yaw": yaw_reward}
        return reward_dict