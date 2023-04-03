from collections import defaultdict
import gymnasium as gym
from gymnasium import spaces
from itertools import islice, tee
import numpy as np
import operator
from tqdm import tqdm

from daw_etal_two_stage import DawEtalTwoStageMDP

## Utilities
def rate_decay(base=0.995, decay=0.999):
    return lambda t: base * (decay ** t)

def unzip(i):
    l, r = tee(i);
    return (list(map(operator.itemgetter(0), l)), list(map(operator.itemgetter(1), r)))

## Agents
class Agent:
    @property
    def env(self): return self._environment

    def __init__(self, environment):
        self._environment = environment

    def get_action(self, observation): raise NotImplementedError

    def update(self,
        observation,
        action,
        reward,
        terminated,
        truncated,
        next_observation): raise NotImplementedError

'''
A Q-Learning implementation for a Gymnasium environment (with a discrete observation/action space).
'''
class QLearning(Agent):
    def __init__(self,
            environment,
            discount_rate=0.95,
            learning_rate=rate_decay(),
            exploration_rate=rate_decay(base=0.1),
            seed=None):
        super().__init__(environment)
        # self.env = environment
        # self.t = 0
        self.training_error = []
        self.rewards = []
        self.rng = np.random.default_rng(seed)

        self.discount_rate = discount_rate
        self._learning_rate = learning_rate
        self._exploration_rate = exploration_rate

        self.q_values = defaultdict(lambda: self.rng.random(self.env.action_space.n))
    
    @property
    def t(self):
        return len(self.training_error)

    @property
    def learning_rate(self):
        return self._learning_rate(self.t)

    @property
    def exploration_rate(self):
        return self._exploration_rate(self.t)

    def get_action(self, observation):
        if self.rng.random() < self.exploration_rate:
            return self.env.action_space.sample()
        else:
            return int(np.argmax(self.q_values[observation]))
    
    def q_update(self,
            observation,
            action,
            reward,
            terminated,
            truncated,
            next_observation):
        future_q_value = (not (terminated or truncated)) * np.max(self.q_values[next_observation])
        temporal_difference = (
            reward + self.discount_rate * future_q_value - self.q_values[observation][action]
        )
        self.q_values[observation][action] = (
            self.q_values[observation][action] + self.learning_rate * temporal_difference
        )
        return temporal_difference

    def update(self,
            observation,
            action,
            reward,
            terminated,
            truncated,
            next_observation):
        temporal_difference = self.q_update(observation, action, reward, 
                                            terminated, truncated, next_observation)
        self.training_error.append(temporal_difference)
        self.rewards.append(reward)
        # if self.t % 1000 == 0:
        #     print(np.mean(list(islice(reversed(self.rewards), 10000))), self.learning_rate, self.exploration_rate)

class DynaQ(QLearning):
    def __init__(self,
            environment,
            discount_rate=0.95,
            learning_rate=rate_decay(),
            exploration_rate=rate_decay(),
            n_simulations=2000,
            seed=None):
        super().__init__(environment, discount_rate, learning_rate, exploration_rate, seed)
        self.n_simulations = n_simulations
        # Dictionary where keys are current states, and values are arrays the size of the action space, 
        # where for each action we have a pair of (reward, next_state).
        # self.model = defaultdict(lambda: np.zeros(self.env.action_space.n, dtype=[('reward', np.float32), ('next_state', np.intc)]))
        self.model = dict()

    def sample_state(self):
        return self.rng.choice(list(map(lambda x: x[0], self.model.keys())))
    
    def sample_action(self, state):
        return self.rng.choice(list(map(lambda x: x[1], filter(lambda x: x[0] == state, self.model.keys()))))

    def update(self,
            observation,
            action,
            reward,
            terminated,
            truncated,
            next_observation):
        
        # Q-learning update
        temporal_difference = self.q_update(observation, action, reward, 
                                            terminated, truncated, next_observation)
        self.training_error.append(temporal_difference)
        
        # Update model (w/ assumption of deterministic environment, see Sutton and Barto)
        self.model[(observation, action)] = (reward, next_observation)

        # simulated Q-learning
        for i in range(self.n_simulations):
            s = self.sample_state()
            a = self.sample_action(s)
            (r, s_) = self.model[(s,a)]
            self.q_update(s, a, r, False, False, s_)

        # if self.t % 10 == 0:
        #     print(np.mean(list(islice(reversed(self.training_error), 10000))), self.learning_rate, self.exploration_rate)

## LoCA Environment and Evaluation

class LoCAEnv(gym.Env):
    def enter_phase_two(self):
        raise NotImplementedError

# def loca_evaluate(agent, environment, n_episodes=1000, seed=None):
    

## Training Agents

def train_episode(agent, environment, seed=None):
    obs, info = environment.reset(seed=seed)
    done = False

    while not done:
        action = agent.get_action(obs)
        # print(action)
        next_obs, reward, terminated, truncated, info = environment.step(action)
        # print(obs, reward)
        # print(environment.render())

        # update the agent
        agent.update(obs, action, reward, terminated, truncated, next_obs)

        # update if the environment is done and the current obs
        done = terminated or truncated
        obs = next_obs

def train(agent, environment, n_episodes=2000):
    for episode in tqdm(range(n_episodes)):
        train_episode(agent, environment, seed=np.random.randint(0, 100000000))

class LoCATwoStageMDP(LoCAEnv):
    metadata = {"render_modes": ["ansi"], "render_fps": 4}
    # Probabilities of transition from state 0 to states 1 and 2.
    probabilities = [
        # Action 0 - "Left". 30% chance to transition to state 1, 70% chance to transition to state 2.
        [0.3, 0.7], 
        # Action 1 - "Right". 70% chance to transition to state 1, 30% chance to transition to state 2.
        [0.7, 0.3]
    ]

    rewards = [
        # Rewards in phase one. State 1 is optimal.
        {0: 0, 1: 4, 2: 2 },
        # Rewards in phase two. State 2 is optimal.
        {0: 0, 1: 1, 2: 2},
    ];

    def __init__(self, 
            render_mode=None,
            episodic=False):
        self.observation_space = spaces.Discrete(3)
        self.action_space = spaces.Discrete(2)

        # self.reward_distributions = tuple(map(lambda x: tuple(unzip(x.items())), rewards))
        self.episodic = episodic

        assert render_mode is None or render_mode in self.metadata["render_modes"]
        self.render_mode = render_mode

        self.loca_phase = 1
    
    def _get_obs(self):
        return self._current_state

    def _get_info(self):
        return None

    def enter_phase_two(self):
        self.loca_phase = 2

    def reset_loca(self):
        self.loca_phase = 1

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.step_count = 0

        if self.loca_phase == 1:
            self._current_state = 0
        else:
            # the black hole is around state 1, so don't exit back to state 0.
            self._current_state = 1

        return self._get_obs(), self._get_info()

    def step(self, action):
        self.step_count += 1
        match self._current_state:
            case 0:
                self._current_state = self.np_random.choice([1,2], p=self.probabilities[action])
                return self._current_state, 0, False, False, self._get_info()
            case s:
                reward = self.rewards[self.loca_phase-1][s]
                self._current_state = 0
                return self._current_state, reward, self.episodic, False, self._get_info()

def main():
    env = DawEtalTwoStageMDP(episodic=True)
    # dyna = DynaQ(env)
    # # train_episode(dyna, env)
    # # # print(dyna.sample_action(dyna.sample_state()))

    # train(dyna, env, n_episodes=20)

    # env = LoCATwoStageMDP(episodic=True)
    # # dyna = DynaQ(env)
    # train_episode(dyna, env)
    # # print(dyna.sample_action(dyna.sample_state()))

    ## LoCA Evaluation
    env = LoCATwoStageMDP(episodic=True)

    naive_loca_eval(QLearning(env), env)
    env.reset_loca()
    naive_loca_eval(DynaQ(env, n_simulations=20), env)

    # env = gym.make("FrozenLake-v1", render_mode="ansi", is_slippery=False) 
    # env.reset()
    # qlearning = QLearning(env)
    # train(qlearning, env, n_episodes=2000000)

    # for line in fileinput.input():
    
def naive_loca_eval(agent, env):
    env.reset()
    # Train on phase one
    train(agent, env, n_episodes=20000)
    # Sample action in state 0. Should yield 1. See probabilities and rewards above.
    sample1 = agent.get_action(0)
    print(sample1, " -- Phase One: ", "PASS" if sample1 == 1 else "FAIL")
    
    # Now enter LoCA phase two
    env.enter_phase_two()
    # Train on phase two
    train(agent, env, n_episodes=20000)
    
    # Sample action in state 0. Should now yield 0, if agent is adaptive. See probabilities and rewards above.
    sample2 = agent.get_action(0)
    print(sample2, " -- Phase Two: ", "PASS" if sample2 == 0 else "FAIL")

if __name__ == "__main__":
    main()
