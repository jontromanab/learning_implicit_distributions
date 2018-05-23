from tree import Tree
import matplotlib.pyplot as plt
import tensorflow as tf
import numpy as np
from utils import *
from itertools import compress

def sample_ball(r, dim=2):
    sample = np.random.random_sample((2)) * (2 * r) - r
    while np.linalg.norm(sample) > r:
        sample = np.random.random_sample((2)) * (2 * r) - r
    return sample

class ESTBatchEnv(object):
    def __init__(self, config, map_info):
        self.config = config
        self.map_info = map_info
        self.state_size = len(self.map_info['start'])

        self.r = 4. # radius of sampling
        self.n = 1 # number of samples to take each iteration
        self.reset()

    def _radius_search(self, node_list, node, radius):

        dists = self.config['dist'](np.array(node_list), node)
        dist_mask = dists < radius

        node_idx = np.where(dist_mask)[0]
        return node_idx

    def _compute_and_update_w(self, node, neighbor_idx, tree_idx):
        tree = self.trees[tree_idx]

        tree.node_info[-1] = 1.0 / (len(neighbor_idx) + 1)
        self.total_weights[tree_idx] += tree.node_info[-1]

        for idx in neighbor_idx:
            w = tree.node_info[idx]
            tree.node_info[idx] = w / (w + 1.)
            self.total_weights[tree_idx] += (tree.node_info[idx] - w)

    def _sample_node(self):
        sample = np.random.random_sample()

        weight_sum = 0
        idx = None
        tree = self.trees[self.tree_idx]
        for i in range(len(tree.node_info)):
            weight_sum += tree.node_info[i] / self.total_weights[self.tree_idx]
            if weight_sum > sample:
                idx = i
                break

        return idx

    def sample_free_space(self, node, r):
        # with eps chance, sample goal
        rand = np.random.random_sample()
        if rand < 0.05:
            goal = self.map_info['goal']
            direction = goal - node
            dir_len = np.linalg.norm(direction)
            if dir_len > 1e-2:
                sample = node + (r * direction / dir_len)
                if not self.config['collision_check'](self.map_info, sample):
                    return sample

        sample = node + sample_ball(r)
        while self.config['collision_check'](self.map_info, sample):
            sample = node + sample_ball(r)
        return sample

    def reset(self):
        self.forward_tree = Tree()
        self.forward_tree.insert_node(self.map_info['start'], 1)
        self.backward_tree = Tree()
        self.backward_tree.insert_node(self.map_info['goal'], 1)
        self.trees = [self.forward_tree, self.backward_tree]
        self.total_weights = [1., 1.] # running total of 1/w(x)
        self.tree_idx = 0
        self.goal_idx = [None, None]

        self.found_path = False
        self.num_collision_checks = 0
        self.samples_drawn = 0


    def __run(self, rand_node, rand_node_idx):
        tree = self.trees[self.tree_idx]
        other_tree = self.trees[1 - self.tree_idx]

        samples = []
        for i in range(self.n):
            samples.append(self.sample_free_space(rand_node, self.r))

        for sample in samples:
            neighbor_idx = self._radius_search(tree.node_states, sample, self.r)
            w = len(neighbor_idx) + 1
            if np.random.sample() > 1.0 / w:
                continue

            path, path_cost = self.config['steer'](rand_node, sample)
            collision, num_checks = self.config['collision_check'](self.map_info, path, True)
            self.num_collision_checks += num_checks

            if collision:
                continue

            new_node = path[-1]
            tree.insert_node(new_node, node_info=1, parent_idx=rand_node_idx)
            self._compute_and_update_w(new_node, neighbor_idx, self.tree_idx)


            # check if forward tree has reached
            if self.tree_idx == 0 and self.config['goal_region'](new_node, self.map_info['goal']):
                self.found_path = True
                self.goal_idx[0] = len(tree.node_states)-1
                break

            # try to link trees
            closest_idx = other_tree.closest_idx(new_node, self.config['dist'])
            closest_node = other_tree.node_states[closest_idx]

            path, path_cost = self.config['steer'](closest_node, new_node)
            collision, num_checks = self.config['collision_check'](self.map_info, path, True)
            self.num_collision_checks += num_checks
            if collision:
                continue
            new_other_node = path[-1]
            neighbor_idx = self._radius_search(other_tree.node_states, new_other_node, self.r)
            other_tree.insert_node(new_other_node, node_info=1, parent_idx=closest_idx)
            self._compute_and_update_w(new_other_node, neighbor_idx, 1-self.tree_idx)

            if np.linalg.norm(new_node - new_other_node) < 1e-1:
                self.found_path = True
                self.goal_idx[1 - self.tree_idx] = len(other_tree.node_states) - 1
                self.goal_idx[self.tree_idx] = len(tree.node_states) - 1

                break


    def run(self, policy):
        self.reset()
        random_sample_list = [[], []]
        num_batch_samples = 1

        count = 0
        while not self.found_path:
            num_tree_samples = len(self.trees[0].node_states) + len(self.trees[1].node_states)
            num_sample = min(num_batch_samples, num_tree_samples)

            while len(random_sample_list[self.tree_idx]) == 0:
                feat_list = []
                sample_list = []

                for i in range(num_sample):
                    sample_idx = self._sample_node()

                    feat = self.config['feat'](sample_idx, 
                        self.trees, 
                        self.map_info,
                        self.tree_idx)
                    feat_list.append(feat)
                    sample_list.append(sample_idx)

                feat_list = np.array(feat_list)

                actions = policy.get_action_multiple(feat_list)

                samples = list(compress(sample_list, actions))
                random_sample_list[self.tree_idx].extend(samples)


            rand_node_idx = random_sample_list[self.tree_idx][0]
            random_sample_list[self.tree_idx].pop(0) # TODO: make more efficient

            rand_node = self.trees[self.tree_idx].node_states[rand_node_idx]

            self.__run(rand_node, rand_node_idx)

            count += 1

            # if count % 10 == 0:
            #     self.show()
            #     plt.show(block=False)
            #     plt.pause(0.1)


    def get_path(self):
        if not self.found_path:
            raise Exception('Path not found yet')

        path1_idx = self.trees[0].path_to_root(self.goal_idx[0])
        path1_idx =  list(reversed(path1_idx))
        path1 = [self.trees[0].node_states[i] for i in path1_idx]
        if self.goal_idx[1] == None:
            # only a forward path
            path = path1
        else:
            path2_idx = self.trees[1].path_to_root(self.goal_idx[1])
            path2 = [self.trees[1].node_states[i] for i in path2_idx]
            path = path1 + path2

        path_len = 0
        for i in range(1, len(path)):
            node1 = path[i]
            node2 = path[i-1]

            path_len += self.config['dist'](np.array([node1]), node2)
        
        return path, path_len


    def show(self):
        plt.cla()
        if self.found_path:
            self.trees[0].show(im=self.map_info['map'], path_idx=len(self.trees[0].node_states)-1)
            self.trees[1].show(goal=self.map_info['goal'], path_idx=len(self.trees[1].node_states)-1)
        else:
            self.trees[0].show(im=self.map_info['map'])
            self.trees[1].show(goal=self.map_info['goal'])

if __name__ == '__main__':
    from functools import partial
    from generate_data import generate_data
    from run_environment import RunEnvironment
    from policy import *
    import time
    from tqdm import tqdm

    # policy = Policy(2)
    # policy.load_model('good_models/models/model_envA2_est/model_envA2_est.ckpt.20.ckpt')

    policy = DefaultPolicy()

    feat = get_feat_flytrap_est
    num_feat = 2

    np.random.seed(0)
    l2_data_dict = generate_data('fly_trap_fixed_a')
    l2_random_sampler = partial(map_sampler_goal_bias, eps=0.1)
    l2_goal = l2_goal_region
    l2_config = {'collision_check': map_collision_check,
              'random_sample': l2_random_sampler,
              'steer': partial(holonomic_steer, extend_length=5.),
              'dist': l2_dist,
              'goal_region': l2_goal,
              'feat': feat,
              'num_feat': num_feat,
              'precomputed': map_obst_precompute(l2_data_dict['map'])}
    np.random.seed(int(time.time()))
    config = l2_config
    data_dict = l2_data_dict

    rrt = ESTBatchEnv(config, data_dict)
    rrt.run(policy)

    # for i in tqdm(range(10)):
    #     rrt.run(policy)

    rrt.show()
    plt.show()

    






