from __future__ import annotations

import copy
import pickle
import random
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    List,
    Optional,
    Self,
    Tuple,
    Type,
    TypeVar,
)

import numpy as np
from gymnasium.core import ActType


def tabular_initial_model_gen(
    replay_buffer: ReplayBuffer[StateType, ActType, ObsType]
) -> InitialModel:
    counter = Counter[StateType]()  # initialize the counter
    for episode in replay_buffer.episodes:
        initial_state = episode.previous_states[0]
        counter[initial_state] += 1  # add to the counter

    def tabular_initial_model(_: StateType) -> StateType:
        # Convert counts to probabilities and sample
        total = sum(counter.values())
        if total == 0:
            raise ValueError("No episodes in replay buffer")

        # Sample from the counter according to frequency
        states = list(counter.keys())
        probs = [count / total for count in counter.values()]
        return random.choices(states, weights=probs, k=1)[
            0
        ]  # choices returns a list, so take first element

    return tabular_initial_model


def empty_reward_model_gen() -> RewardModel:
    def empty_reward_model(
        state: StateType, action: ActType, next_state: StateType
    ) -> Tuple[float, bool]:
        return 0.0, False

    return empty_reward_model


def empty_observation_model_gen() -> ObservationModel:
    def empty_observation_model(
        state: StateType, action: ActType, empty_obs: ObsType
    ) -> ObsType:
        return empty_obs

    return empty_observation_model


def empty_transition_model_gen() -> TransitionModel:
    def empty_transition_model(state: StateType, action: ActType) -> StateType:
        return state

    return empty_transition_model


class State(ABC):
    def copy(self) -> Self:
        return copy.deepcopy(self)

    @abstractmethod
    def __repr__(self) -> str:
        pass

    def distance(self, other: State) -> float:
        if other == self:
            return 0.0
        else:
            return float("inf")

    @abstractmethod
    def encode(self) -> Any:
        """Converts the state to a representation that the language model is
        expecting."""
        pass

    @classmethod
    @abstractmethod
    def decode(cls: Type[Self], encoded: Any) -> Self:
        """Converts the encoding back to the state representation."""
        pass

    @abstractmethod
    def __hash__(self) -> int:
        pass

    def __eq__(self, other: object) -> bool:
        return self.__hash__() == other.__hash__()


StateType = TypeVar("StateType", bound=State)


class Observation(ABC, Generic[StateType]):
    def copy(self) -> Self:
        return copy.deepcopy(self)

    @abstractmethod
    def __hash__(self) -> int:
        pass

    def distance(self, other: State) -> float:
        if other == self:
            return 0.0
        else:
            return float("inf")

    @abstractmethod
    def __repr__(self) -> str:
        pass

    @abstractmethod
    def encode(self) -> Any:
        """Converts the state to a representation that the language model is
        expecting."""
        pass

    @classmethod
    @abstractmethod
    def decode(cls: Type[Self], encoded: Any) -> Self:
        """Converts the encoding back to the state representation."""
        pass


ObsType = TypeVar("ObsType", bound=Observation)


class Environment(ABC, Generic[StateType, ActType, ObsType]):
    def __init__(self, *args: Any, max_steps: int = 0, **kwargs: Any):
        self.max_steps = max_steps

    @abstractmethod
    def step(
        self, action: ActType
    ) -> Tuple[Observation, State, float, bool, bool, Dict[str, Any]]:
        pass

    @abstractmethod
    def reset(self, seed: int = 0) -> State:
        pass

    def visualize_episode(
        self,
        states: List[StateType],
        observations: List[ObsType],
        actions: List[ActType],
        episode_num: int,
    ) -> None:
        pass

    def visualize_belief(self, belief: Belief, episode_num: int) -> None:
        pass


class Belief(ABC, Generic[StateType, ObsType]):
    def copy(self) -> Self:
        return copy.deepcopy(self)

    @abstractmethod
    def sample(self) -> Optional[StateType]:
        pass

    @abstractmethod
    def log_prob(self, state: StateType) -> float:
        pass


class ParticleBelief(Belief[StateType, ObsType]):
    def __init__(self, particles: Dict[StateType, int] = {}) -> None:
        self.particles = particles
        super(ParticleBelief, self).__init__()

    @property
    def num_particles(self) -> int:
        return sum(self.particles.values())

    def sample(self) -> StateType:
        probs = [v / float(self.num_particles) for v in self.particles.values()]
        return random.choices(list(self.particles.keys()), weights=probs, k=1)[0]

    def sample_k(self, k: int) -> List[StateType]:
        probs = [v / float(self.num_particles) for v in self.particles.values()]
        return random.choices(list(self.particles.keys()), weights=probs, k=k)

    def log_prob(self, state: StateType) -> float:
        return np.log(self.particles[state] / float(self.num_particles))

    def to_list(self) -> List[StateType]:
        """Return a flat list of states where each state appears according to
        its count."""
        return [state for state, count in self.particles.items() for _ in range(count)]

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ParticleBelief):
            return False
        return hash(other) == hash(self)

    def __hash__(self) -> int:
        return hash(frozenset(self.particles.items()))

    def __len__(self) -> int:
        return sum(self.particles.values())

    @classmethod
    def from_list(cls, states: List[StateType]) -> ParticleBelief:
        """Create a ParticleBelief from a list of state samples by counting how
        many times each state appears."""
        counts = Counter(states)
        return cls(particles=dict(counts))


class CategoricalBelief(Belief[StateType, ObsType]):
    def __init__(self, dist: Dict[StateType, float] = {}) -> None:
        self.dist = dist
        self.quantization = 9
        assert round(sum(dist.values()), self.quantization) == 1.0
        super(CategoricalBelief, self).__init__()

    def sample(self) -> StateType:
        return random.choices(
            list(self.dist.keys()), weights=list(self.dist.values()), k=1
        )[0]

    def sample_k(self, k: int) -> List[StateType]:
        return random.choices(
            list(self.dist.keys()), weights=list(self.dist.values()), k=k
        )

    def get_entropy(self) -> float:
        """Calculate and return the entropy of the categorical belief
        distribution.

        Entropy is defined as: H = -∑ p * log(p) for each probability p in the distribution.
        This measures the uncertainty of the belief.
        """
        return -sum(p * np.log(p) for p in self.dist.values() if p > 0)

    def log_prob(self, state: StateType) -> float:
        return np.log(self.dist[state])

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, CategoricalBelief):
            return False

        quant_self = {k: round(v, self.quantization) for k, v in self.dist.items()}
        quant_other = {k: round(v, self.quantization) for k, v in other.dist.items()}
        return quant_self == quant_other

    def __hash__(self) -> int:
        # Quantize the probabilities by rounding to 9 decimal places.
        quantized_items = frozenset(
            (key, round(prob, self.quantization)) for key, prob in self.dist.items()
        )
        return hash(quantized_items)

    @classmethod
    def from_list(cls, states: List[StateType]) -> CategoricalBelief:
        """Create a ParticleBelief from a list of state samples by counting how
        many times each state appears."""
        dist = {k: v / len(states) for k, v in dict(Counter(states)).items()}
        return cls(dist=dist)


BeliefType = TypeVar("BeliefType", bound=Belief)
RewardModel = Callable[[StateType, ActType, StateType], Tuple[float, bool]]
InitialModel = Callable[[StateType], StateType]
ObservationModel = Callable[[StateType, ActType, ObsType], ObsType]
TransitionModel = Callable[[StateType, ActType], StateType]
Heuristic = Callable[[StateType], float]
TypeTuple = Tuple[type[StateType], type[ObsType]]


def zero_heuristic(state: StateType) -> float:
    return 0


@dataclass
class Episode(Generic[StateType, ActType, ObsType, BeliefType]):
    previous_states: List[StateType] = field(default_factory=list)
    next_states: List[StateType] = field(default_factory=list)
    actions: List[ActType] = field(default_factory=list)
    rewards: List[float] = field(default_factory=list)
    terminated: List[bool] = field(default_factory=list)
    next_observations: List[ObsType] = field(default_factory=list)
    length: int = 0


@dataclass
class Transition(Generic[StateType, ActType]):
    prev_state: StateType
    action: ActType
    next_state: StateType
    reward: float
    terminated: bool


class ReplayBuffer(Generic[StateType, ActType, ObsType]):
    def __init__(self, episodes: List[Episode] = []) -> None:
        self.episodes = episodes
        self.current_episode: Optional[Episode] = None

    def append_episode_step(
        self,
        prev_state: StateType,
        state: StateType,
        action: ActType,
        obs: ObsType,
        true_reward: float,
        terminated: bool,
    ) -> None:
        if self.current_episode is None:
            self.current_episode = Episode()

        self.current_episode.previous_states.append(prev_state)
        self.current_episode.next_states.append(state)
        self.current_episode.actions.append(action)
        self.current_episode.next_observations.append(obs)
        self.current_episode.rewards.append(true_reward)
        self.current_episode.terminated.append(terminated)
        self.current_episode.length += 1

    @property
    def transitions(self) -> List[Transition[StateType, ActType]]:
        """Returns a list of all transitions in the replay buffer.

        This will create a list of Transition objects from all episodes
        stored in the replay buffer.
        """
        transitions_list = []
        for episode in self.episodes:
            for i in range(episode.length):
                transition = Transition(
                    prev_state=episode.previous_states[i],
                    action=episode.actions[i],
                    next_state=episode.next_states[i],
                    reward=episode.rewards[i],
                    terminated=episode.terminated[i],
                )
                transitions_list.append(transition)
        return transitions_list

    def wrap_up_episode(self) -> None:
        assert isinstance(self.current_episode, Episode)
        self.episodes.append(self.current_episode)
        self.current_episode = None

    def get_episode(self, eps_number: int) -> Episode:
        """Assume `eps_number` is 1-indexed."""
        try:
            return self.episodes[eps_number]
        except IndexError as exc:
            raise ValueError(f" eps_number {eps_number} is out of bounds.") from exc

    def sample_transitions(self, n: int) -> List[Transition]:
        """Samples `n` transitions uniformly from replay buffer.

        Returns all the transitions if number of transitions is less than `n`.
        """
        # If we have fewer transitions than requested, return all transitions
        if len(self.transitions) <= n:
            return self.transitions

        # Assuming Transition is a defined type
        transitions = np.array(self.transitions)
        sampled_transitions = np.random.choice(transitions, size=n).tolist()
        return list(sampled_transitions)  # Explicitly cast to a Python list

    def save_to_file(self, file_path: str) -> None:
        """Saves the current ReplayBuffer instance to a file using pickle."""
        with open(file_path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load_from_file(cls, file_path: str) -> "ReplayBuffer":
        """Loads a ReplayBuffer instance from a pickle file.

        Raises a ValueError if the loaded object is not an instance of
        ReplayBuffer.
        """
        with open(file_path, "rb") as f:
            obj = pickle.load(f)
        if not isinstance(obj, cls):
            raise ValueError("Loaded object is not of type ReplayBuffer")
        return obj


def tabular_reward_model_gen(
    replay_buffer: ReplayBuffer[StateType, ActType, ObsType]
) -> RewardModel:
    # Track (state, action, next_state) -> (total_reward, count, termination_count)
    rewards_dict: Dict[Tuple[StateType, ActType, StateType], List[float]] = {}
    for transition in replay_buffer.transitions:
        key = (transition.prev_state, transition.action, transition.next_state)
        if key not in rewards_dict:
            rewards_dict[key] = [0.0, 0, 0]
        rewards_dict[key][0] += transition.reward
        rewards_dict[key][1] += 1
        rewards_dict[key][2] += 1 if transition.terminated else 0

    def tabular_reward_model(
        state: StateType, action: ActType, next_state: StateType
    ) -> Tuple[float, bool]:
        key = (state, action, next_state)
        if key not in rewards_dict or rewards_dict[key][1] == 0:
            return 0.0, False

        avg_reward = rewards_dict[key][0] / rewards_dict[key][1]
        termination_prob = rewards_dict[key][2] / rewards_dict[key][1]
        is_terminated = random.random() < termination_prob

        return avg_reward, is_terminated

    return tabular_reward_model


def tabular_transition_model_gen(
    replay_buffer: ReplayBuffer[StateType, ActType, ObsType]
) -> TransitionModel:
    # Track (state, action) -> {next_state: count}
    transitions_dict: Dict[Tuple[StateType, ActType], Counter[StateType]] = {}
    for transition in replay_buffer.transitions:
        key = (transition.prev_state, transition.action)
        if key not in transitions_dict:
            transitions_dict[key] = Counter[StateType]()
        transitions_dict[key][transition.next_state] += 1

    def tabular_transition_model(state: StateType, action: ActType) -> StateType:
        key = (state, action)
        if key not in transitions_dict:
            return state  # Return current state if transition not seen

        # Sample next state according to empirical distribution
        next_states = list(transitions_dict[key].keys())
        counts = list(transitions_dict[key].values())
        total = sum(counts)
        probs = [count / total for count in counts]

        return random.choices(next_states, weights=probs, k=1)[0]

    return tabular_transition_model


def empty_initial_model_gen() -> InitialModel:
    def empty_initial_model(empty_state: State) -> State:
        return empty_state

    return empty_initial_model


def tabular_observation_model_gen(
    replay_buffer: ReplayBuffer[StateType, ActType, ObsType],
    type_tuple: Tuple[type[StateType], type[ObsType]],
) -> ObservationModel:
    # Track state -> {observation: count}
    observation_dict: Dict[Tuple[StateType, ActType], Dict[ObsType, int]] = {}
    for episode in replay_buffer.episodes:
        for state, action, obs in zip(
            episode.next_states, episode.actions, episode.next_observations
        ):
            key = (state, action)
            if key not in observation_dict:
                observation_dict[key] = Counter()
            observation_dict[key][obs] += 1

    def tabular_observation_model(
        state: StateType, action: ActType, empty_obs: ObsType
    ) -> ObsType:
        key = (state, action)
        if key not in observation_dict:
            # Return empty observation
            return empty_obs

        # Sample observation according to empirical distribution
        observations = list(observation_dict[key].keys())
        counts = list(observation_dict[key].values())
        total = sum(counts)
        probs = [count / total for count in counts]

        return random.choices(observations, weights=probs, k=1)[0]

    return tabular_observation_model
