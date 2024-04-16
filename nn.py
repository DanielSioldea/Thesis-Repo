# IMPORTS
import warnings
warnings.filterwarnings("ignore")
import os
import gym
import sys
import optparse
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt

# SET UP SUMO TRACI CONNECTION
if 'SUMO_HOME' in os.environ:
    tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
    sys.path.append(tools)
else:
    sys.exit("Please declare environment variable 'SUMO_HOME'")

# TRACI IMPORTS (IDK IF THIS NEEDS TO BE HERE OR CAN BE AT THE TOP)
from sumolib import checkBinary
import traci
import traci.constants as tc


# OPTIONS (CUS WHY NOT)
def get_options():
    optParser = optparse.OptionParser()
    optParser.add_option("--nogui", action="store_true",
                         default=False, help="run the commandline version of sumo")
    options, args = optParser.parse_args()
    return options

## NEURAL NETWORK
class TrafficController(nn.Module):
    def __init__(self, input_size, hidden1_size, hidden2_size, output_size):
        super(TrafficController, self).__init__()
        self.input_size = input_size
        self.hidden1_size = hidden1_size
        self.hidden2_size = hidden2_size
        self.output_size = output_size

        #self.flatten = nn.Flatten()
        self.hidden1 = nn.Linear(input_size, hidden1_size)      # FIRST HIDDEN LAYER HAS 12 NEURONS
        self.activ1 = nn.ReLU()                                 # ReLU ACTIVATION FUNCTION
        self.hidden2 = nn.Linear(hidden1_size, hidden2_size)    # SECOND HIDDEN LAYER HAS 8 NEURONS
        self.activ2 = nn.ReLU()                                 # ReLU ACTIVATION FUNCTION
        self.output = nn.Linear(hidden2_size, output_size)      # OUTPUT LAYER HAS ONE NEURON
        self.activ_out = nn.Sigmoid()                           # SIGMOID ACTIVATION FUNCTION (ENSURES OUTPUT BETWEEN 0 AND 1)

        # OPTIMIZER AND LOSS FUNCTION (TEST DIFFERENT OPTIONS)
        self.optim = optim.Adam(self.parameters(), lr=0.001)
        self.loss = nn.MSELoss()
        # SELECT GPU OR CPU DEPENDING ON WHAT IS AVAILABLE
        self.device = ("cuda" if torch.cuda.is_available() else "cpu")
        self.to(self.device)

        print(f"Network is using {self.device} device.")
    
    # DEFINE HOW NN FEEDS FORWARD INTO LAYERS
    def forward(self, x):
        x = self.activ1(self.hidden1(x))
        x = self.activ2(self.hidden2(x))
        x = self.activ_out(self.output(x))
        return x
    
## DEFINE AGENT CLASS FOR LEARNING
class TrafficAgent:
    def __init__(self, gamma, epsilon, lr, input_size, hidden1_size, hidden2_size, n_actions, max_memory_size, batch_size):
        self.gamma = gamma
        self.epsilon = epsilon
        self.lr = lr
        self.input_size = input_size
        self.hidden1_size = hidden1_size
        self.hidden2_size = hidden2_size
        self.n_actions = n_actions
        max_memory_size = 100000
        self.mem_size = max_memory_size
        self.mem_cntr = 0
        self.batch_size = batch_size

        self.q_eval = TrafficController(self.input_size, self.hidden1_size, self.hidden2_size, self.n_actions)

    def choose_action(self, observation):
        if np.random.random() > self.epsilon:
            state = torch.tensor([observation], dtype=torch.float32)
            actions = self.q_eval.forward(state)
            action = torch.argmax(actions).item()
        else:
            action = np.random.choice(self.n_actions)
        return action
    
    def learn(self):
        self.q_eval.optim.zero_grad()
        if self.mem_cntr < self.batch_size:
            return
        max_mem = min(self.mem_cntr, self.mem_size)
        batch = np.random.choice(max_mem, self.batch_size, replace=False)

        state_batch = self.state_memory[batch]
        action_batch = self.action_memory[batch]
        reward_batch = self.reward_memory[batch]
        new_state_batch = self.new_state_memory[batch]
        done_batch = self.terminal_memory[batch]

        state_batch = torch.tensor(state_batch).to(self.q_eval.device)
        action_batch = torch.tensor(action_batch).to(self.q_eval.device)
        reward_batch = torch.tensor(reward_batch).to(self.q_eval.device)
        new_state_batch = torch.tensor(new_state_batch).to(self.q_eval.device)
        done_batch = torch.tensor(done_batch).to(self.q_eval.device)

        q_eval = self.q_eval.forward(state_batch).gather(1, action_batch.unsqueeze(1)).squeeze(1)
        q_next = self.q_eval.forward(new_state_batch).max(dim=1)[0]
        q_next[done_batch] = 0.0
        q_target = reward_batch + self.gamma * q_next

        loss = self.q_eval.loss(q_target, q_eval).to(self.q_eval.device)
        loss.backward()
        self.q_eval.optim.step()

# OBTAIN QUEUE LENGTHS AND TIMES
def queue_info(lane):
    queue_info = []
    queue_length = 0
    queue_time = 0
    total_queue_len = 0
    total_queue_time = 0
    for i in lane:
        # queue_length = traci.junction.getParameter(i, "waitingCount")
        queue_length = traci.lane.getLastStepHaltingNumber(i)
        # if queue_length > 0:
        #     print(f"Queue length: {queue_length} for lane {i}")
        # queue_time = traci.junction.getParameter(i, "waitingTime")
        queue_time = traci.lane.getWaitingTime(i)
        # if queue_time > 0:
        #     print(f"Queue time: {queue_time} for lane {i}")
        if queue_length:
            queue_length_int = int(queue_length)
        else:
            queue_length_int = 0
        if queue_time:
            queue_time_int = int(queue_time)
        else:
            queue_time_int = 0
        queue_info.append([queue_length_int, queue_time_int])
        total_queue_len += queue_length_int
        total_queue_time += queue_time_int
    return queue_info, total_queue_len, total_queue_time

# def get_edge_length(edge_id):
#     lanes = traci.edge.getLaneNumber(edge_id)
#     length = sum(traci.lane.getLength(f"{edge_id}_{i}") for i in range(lanes))
#     return length

# def num_vehicles(edges):
#     num_vehicles = dict()
#     for i in edges:
#         num_vehicles[i] = 0
#         # lane_length = traci.lane.getLength(i)
#         # edge_length = get_edge_length(i)
#         for j in traci.edge.getLastStepVehicleIDs(i):
#             vehicle_pos = traci.vehicle.getLanePosition(j)
#             lane_length = traci.lane.getLength(traci.vehicle.getLaneID(j))
#             if lane_length - vehicle_pos <= 100 and traci.vehicle.getSpeed(j) == 0:
#             # if edge_length - traci.vehicle.getLanePosition(j) <= 50:
#                 num_vehicles[i] += 1
#         print(f"Number of vehicles: {num_vehicles[i]} in edge {i}")
#         # print(f"The length of edge {i} is {edge_length}")
#     return num_vehicles

# def num_vehicles(edges):
#     num_vehicles = dict()
#     for i in edges:
#         num_vehicles[i] = traci.edge.getLastStepHaltingNumber(i)
#         print(f"Number of vehicles: {num_vehicles[i]} in edge {i}")
#     return num_vehicles

def get_vehicle_number(lanes):
    vehicles_per_lane = dict()
    for i in lanes:
        vehicles_per_lane[i] = 0
        for j in traci.lane.getLastStepVehicleIDs(i):
            if traci.vehicle.getSpeed(j) == 0:
                vehicles_per_lane[i] += 1
        print(f"Number of vehicles: {vehicles_per_lane[i]} in lane {i}")
    return vehicles_per_lane


# ADJUST TRAFFIC LIGHTS
def adjust_traffic_light(junctions, junc_time, junc_state):
    for i, junction in enumerate(junctions):
        traci.trafficlight.setRedYellowGreenState(junction, junc_state[i])
        traci.trafficlight.setPhaseDuration(junction, junc_time[i])

# MAIN FUNCTION
def main():
    # GET OPTIONS
    options = get_options()
    avg_losses = []
    # START SUMO
    sumoBinary = checkBinary('sumo')
    # traci.start([sumoBinary, "-c", "Data\Test1\mainofframp.sumocfg"])
    traci.start([sumoBinary, "-c", "Data\Test2\SmallGrid.sumocfg"])
    
    # DEFINE JUNCTIONS AND LANES
    junctions = traci.trafficlight.getIDList()
    print(f"Junctions: {junctions}")
    # num_junctions = list(range(len(junctions)))
    num_junctions = len(junctions)
    print(f"Number of junctions: {num_junctions}")
    lanes = traci.lane.getIDList()
    num_lanes = len(lanes)
    print(f"Lanes: {lanes}")
    # edges = traci.edge.getIDList()
    edges = [edge for edge in traci.edge.getIDList() if not edge.startswith(':')]
    num_edges = len(edges)
    print(f"Edges: {edges}")
    end_time = traci.simulation.getEndTime()
    # print(f"End time: {end_time}")
    
    # DEFINE NETWORK PARAMETERS
    input_size = 2 * num_lanes
    hidden1_size = 16
    hidden2_size = 8
    output_size = 2 * num_junctions
    epochs = 1
    
    # CREATE MODEL
    model = TrafficController(input_size, hidden1_size, hidden2_size, output_size)

    # TRAIN MODEL

    for epoch in range(epochs):
        step = 0
        total_loss = 0
        num_iters = 0
        while step < (end_time + 5):
            # SIMULATION STEP
            traci.simulationStep()
            step += 1
            # num_vehicles(edges)
            print(f"Step {step}")
            get_vehicle_number(lanes)
            # GET QUEUE LENGTHS AND TIMES
            queue_data, total_length, total_time = queue_info(lanes)
            # print(f"Queue data: {queue_data}")
        
            input_data = torch.tensor(sum(queue_data, []), dtype=torch.float32)
            # print(f"Input data: {input_data}")

            # FORWARD PASS
            output_data = model(input_data)
            # print(f"Output data: {output_data}")

            # DEFINE OUTPUTS
            junc_state = output_data[:output_size // 2]
            junc_time = output_data[output_size // 2:]
            # print(f"Junction State: {junc_state}")
            # print(f"Junction Time: {junc_time}")
            

            # ADJUST TRAFFIC LIGHTS
            adjust_traffic_light(junctions, junc_time, junc_state)

            # GET REWARD
            # reward = -1
            reward = -total_time -1 * total_length

            # CALCULATE LOSS
            loss = model.loss(output_data, torch.tensor([reward], dtype=torch.float32))

            # BACKWARD PASS AND OPTIMIZE
            model.optim.zero_grad()
            loss.backward()
            model.optim.step()

            total_loss += loss.item()
            num_iters += 1

            # current_state = traci.trafficlight.getRedYellowGreenState(junctions[0])
            # print(f"Current state: {current_state}")
        
        avg_loss = total_loss / num_iters
        avg_losses.append(avg_loss)

            # PRINT LOSS
        if (epoch + 1) % 1 == 0:
            print(f"Epoch {epoch + 1}/{epochs}, Loss: {loss.item()}")

        # CLOSE SUMO
    traci.close()

    # PLOT LOSSES
    plt.plot(range(1, epochs + 1), avg_losses)
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.title("Training Loss")
    plt.show()
    plt.savefig("Figures\TrainingLoss-mainofframp-100epochs.png")

if __name__ == "__main__":
    main()