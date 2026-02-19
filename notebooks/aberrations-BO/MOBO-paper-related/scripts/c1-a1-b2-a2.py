# %%
import sys
import ast
sys.path.insert(0, '../')
from asyncroscopy.clients.notebook_client import NotebookClient
import matplotlib.pyplot as plt

import pyTEMlib
from pyTEMlib import probe_tools as pt

# %% [markdown]
# ### Connections:

# %%
# Connect the Client to the central (async) server
tem = NotebookClient.connect(host='localhost',port=9000)

# Tell the central server address of all connected instruments
routing_table= {"AS": ("localhost", 9001),
                "Gatan": ("localhost", 9002),
                "Ceos": ("localhost", 9003),
                "Preacquired_AS": ("localhost", 9004)}
tem.send_command('Central',"set_routing_table", routing_table)

# ConnectionResetError: [Errno 54] Connection reset by peer 
# in terminal, type:
# lsof -i :9000

# %%
# connect to the AutoScript computer and initialize microscope
tem.send_command('AS',command='connect_AS',args={'host':'10.46.217.241','port':9095})

# %%
tem.send_command(destination = 'Ceos', command = 'getInfo', args = {})

# %% [markdown]
# ### Help commands:

# %%
# Now that we're routed to all instruments,
# let's take an inventory of commands available on each instrument
cmds = tem.send_command('AS', 'discover_commands')
print(cmds)

# %%
# These two are working, but should be much better.
tem.send_command('AS', command='get_help', args={'command_name':'connect_AS'})

_current_c10  = 0.0
_current_c12a = 0.0
_current_c12b = 0.0
_current_c21a = 0.0
_current_c21b = 0.0
_current_c23a = 0.0
_current_c23b = 0.0
image_args = {'scanning_detector': 'HAADF', 'size': 512, 'dwell_time': 10e-6}

# %%
import matplotlib.pyplot as plt
import numpy as np


def contrast_rms(im, eps=1e-12):
    m = np.mean(im)
    return np.std(im) / (m + eps)

def calculate_fft_score(im, mask_radius=10):
    fft = np.fft.fft2(im)
    fft_shift = np.fft.fftshift(fft)
    magnitude_log = np.log(1 + np.abs(fft_shift))

    rows, cols = im.shape
    crow, ccol = rows // 2, cols // 2
    magnitude_log[crow-mask_radius:crow+mask_radius, ccol-mask_radius:ccol+mask_radius] = 0

    score = np.mean(magnitude_log)
    return score, magnitude_log

def get_stem_image_contrast_and_fft(c10, c12a, c12b, c21a, c21b, c23a, c23b, plot_diagnostics=False):
    # Bring in globals
    global _current_c10, _current_c12a, _current_c12b
    global _current_c21a, _current_c21b, _current_c23a, _current_c23b
    global image_args

    # ---- 1. Compute Deltas (Target - Current) ----
    dc10  = c10  - _current_c10
    dc12a = c12a - _current_c12a
    dc12b = c12b - _current_c12b
    dc21a = c21a - _current_c21a
    dc21b = c21b - _current_c21b
    dc23a = c23a - _current_c23a
    dc23b = c23b - _current_c23b

    # ---- 2. Apply Deltas Group by Group ----
    
    # C1 (Defocus)
    tem.send_command(
        destination='Ceos',
        command='correctAberration',
        args={'name': "C1", 'value': dc10 * 1e-9, 'select': "coarse"}
    )
    
    # A1 (2-fold Astigmatism)
    tem.send_command(
        destination='Ceos',
        command='correctAberration',
        args={'name': "A1", 'value': (dc12a * 1e-9, dc12b * 1e-9), 'select': "coarse"}
    )
    
    # B2 (Axial Coma)
    tem.send_command(
        destination='Ceos',
        command='correctAberration',
        args={'name': "B2", 'value': (dc21a * 1e-9, dc21b * 1e-9), 'select': "coarse"}
    )
    
    # A2 (3-fold Astigmatism)
    tem.send_command(
        destination='Ceos',
        command='correctAberration',
        args={'name': "A2", 'value': (dc23a * 1e-9, dc23b * 1e-9), 'select': "coarse"}
    )

    # ---- 3. Update Tracked State ----
    _current_c10  = c10
    _current_c12a = c12a
    _current_c12b = c12b
    _current_c21a = c21a
    _current_c21b = c21b
    _current_c23a = c23a
    _current_c23b = c23b

    # ---- 4. Acquire Image ----
    # image_args = {'scanning_detector': 'HAADF', 'size': 512, 'dwell_time': 10e-6}
    sim_im = tem.send_command('AS', 'get_scanned_image', image_args)
    tem.send_command('AS', 'blank_beam', {})
    sim_array = np.array(sim_im, dtype=float)

    # ---- 5. Metrics ----
    score, fft_log = calculate_fft_score(sim_array)
    contrast = contrast_rms(sim_array)

    if plot_diagnostics:
        fig, ax = plt.subplots(1, 2, figsize=(10, 5))

        ax[0].imshow(sim_array, cmap='gray')
        ax[0].set_title(f'Image\nContrast={contrast:.4f}')
        ax[0].axis('off')

        ax[1].imshow(fft_log, cmap='inferno')
        ax[1].set_title(f'FFT Score={score:.4f}')
        ax[1].axis('off')

        plt.tight_layout()
        plt.show()

    return contrast, score, sim_im

# %% [markdown]
# ### 2.2 Setup seed data for the BO

# %%
# Parameter ranges for C1, A1, B2, A2
param_ranges = {
    # 1st Order
    'C10': (-20, 20), 'C12a': (-20, 20), 'C12b': (-20, 20),
    # 2nd Order
    'C21a': (-50, 50), 'C21b': (-50, 50),
    'C23a': (-50, 50), 'C23b': (-50, 50),
}


# %%
# New: Sobol for 7 Dimensions
import torch
from botorch.utils.sampling import draw_sobol_samples

# Define bounds tensor explicitly
bounds_tensor = torch.tensor([
    [param_ranges['C10'][0], param_ranges['C12a'][0], param_ranges['C12b'][0], 
     param_ranges['C21a'][0], param_ranges['C21b'][0], param_ranges['C23a'][0], param_ranges['C23b'][0]],
    
    [param_ranges['C10'][1], param_ranges['C12a'][1], param_ranges['C12b'][1], 
     param_ranges['C21a'][1], param_ranges['C21b'][1], param_ranges['C23a'][1], param_ranges['C23b'][1]]
], dtype=torch.float64)

n_seed = 16  # Recommended: > 2 * num_dimensions
seed_points_tensor = draw_sobol_samples(bounds=bounds_tensor, n=n_seed, q=1).squeeze(1)
seed_points = seed_points_tensor.numpy()

# %%
# %%
import os
import time
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import json


def init_experiment(name):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base_dir = f"data/{name}_{timestamp}"
    os.makedirs(f"{base_dir}/images", exist_ok=True)
    print(f"Logging to: {base_dir}")
    return base_dir, []

def save_step(base_dir, history, step, phase, params_dict, contrast, fft_score, 
              train_time, acq_time, hw_time, step_time, img):
    row = {
        'step': step, 
        'phase': phase,   # <--- NEW: Explicitly logs "SEED" or "BO"
        'contrast': contrast, 
        'fft_score': fft_score, 
        'time_gp_train': train_time,
        'time_acq_opt': acq_time,
        'time_hardware': hw_time,
        'time_total_step': step_time
    }
    row.update(params_dict) 
    history.append(row)
    
    pd.DataFrame(history).to_csv(f"{base_dir}/log.csv", index=False)
    
    fname = f"step_{step:03d}_{phase}_C_{contrast:.3f}.png" # Optional: Add phase to filename too
    plt.imsave(f"{base_dir}/images/{fname}", img, cmap='gray')
    np.save(f"{base_dir}/images/step_{step:03d}.npy", img)

def save_config(base_dir, param_ranges, image_args):
    """Saves the search ranges and image args to a config file"""
    config = {
        "param_ranges": param_ranges,
        "image_args": image_args,
    }

    with open(f"{base_dir}/config.json", "w") as f:
        json.dump(config, f, indent=4)

    print(f"Config saved to {base_dir}/config.json")


# %%

import torch

# --- Setup ---
exp_dir, history = init_experiment("Full_7Dim_Opt")
# Reset all 7 globals


save_config(exp_dir, param_ranges, image_args)
# ========== QUERY SEED POINTS ==========
print(f"\nQuerying {n_seed} seed points...")
seed_scores = []
seed_images = []
global_step = 0


for i, (c10, c12a, c12b, c21a, c21b, c23a, c23b) in enumerate(seed_points):
    t0 = time.perf_counter()

    contrast, fft_score, sim_im = get_stem_image_contrast_and_fft(
        c10, c12a, c12b, c21a, c21b, c23a, c23b, plot_diagnostics=True
    )
    t_hw = time.perf_counter() - t0
    phase = "SEED"
    params = {
        'C10': c10, 'C12a': c12a, 'C12b': c12b,
        'C21a': c21a, 'C21b': c21b, 'C23a': c23a, 'C23b': c23b
        }
    save_step(exp_dir, history, global_step, phase, params, contrast, fft_score, 0.0, 0.0, t_hw, t_hw, sim_im)
    global_step += 1

    rewards = np.array([contrast, fft_score])
    seed_scores.append(rewards)
    seed_images.append(sim_im)
    
seed_scores = np.array(seed_scores)

# %%
plt.scatter(seed_scores[:, 0], seed_scores[:, 1])

# %% [markdown]
# ### 2.3 Simple MOBO 

# %%
import torch
from botorch.models import SingleTaskGP
from botorch.models.transforms import Normalize, Standardize
from botorch.fit import fit_gpytorch_mll
from botorch.acquisition import LogExpectedImprovement
from botorch.optim import optimize_acqf
from gpytorch.mlls import ExactMarginalLogLikelihood
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D


from botorch.models import MultiTaskGP
from botorch.acquisition.multi_objective import qExpectedHypervolumeImprovement, qLogExpectedHypervolumeImprovement 
from botorch.utils.multi_objective.box_decompositions import NondominatedPartitioning
from botorch.utils.multi_objective import is_non_dominated
from botorch.utils.sampling import sample_simplex
from botorch.utils.transforms import normalize, unnormalize

# Set random seeds
np.random.seed(42)
torch.manual_seed(42)

# %%
# ========== INITIAL SEED POINTS (from previous code) ==========
print(f"Starting with {n_seed} seed points...")
print(f"Best initial contrast: {seed_scores.max():.4f}")

# Convert to tensors
train_X = torch.tensor(seed_points, dtype=torch.float64)
train_Y = torch.tensor(seed_scores, dtype=torch.float64)

# Define bounds for optimization (4 Parameters)
bounds = torch.tensor([
    [param_ranges['C10'][0], param_ranges['C12a'][0], param_ranges['C12b'][0], 
     param_ranges['C21a'][0], param_ranges['C21b'][0], param_ranges['C23a'][0], param_ranges['C23b'][0]],  # lower bounds
    
    [param_ranges['C10'][1], param_ranges['C12a'][1], param_ranges['C12b'][1], 
     param_ranges['C21a'][1], param_ranges['C21b'][1], param_ranges['C23a'][1], param_ranges['C23b'][1]]   # upper bounds
], dtype=torch.float64)





# ---- device & dtype ----
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dtype = torch.float64

torch.set_default_dtype(dtype)

# move inputs/bounds to device+dtype
train_X = train_X.to(device=device, dtype=dtype)
train_Y = train_Y.to(device=device, dtype=dtype)
bounds  = bounds.to(device=device, dtype=dtype)

n_bo_steps = 20
all_X = train_X.clone()
all_Y = train_Y.clone()
all_images = seed_images.copy()

print("\n" + "="*60)
print("Starting Multi-Objective Bayesian Optimization with EHVI")
print("="*60)

ref_point = train_Y.min(dim=0).values - 0.1 * train_Y.std(dim=0)

print(f"Reference point: {ref_point.detach().cpu().numpy()}")

for step in range(n_bo_steps):
    t_step_start = time.perf_counter() # <--- START TOTAL TIMER

    print(f"\n--- BO Step {step + 1}/{n_bo_steps} ---")
    
    # Train GP (model follows tensor's device/dtype)
    print("Training Multi-Output GP...")
    t0 = time.perf_counter()

    gp_model = SingleTaskGP(
        all_X, all_Y,
        input_transform=Normalize(d=all_X.shape[-1]).to(device=device, dtype=dtype),
        outcome_transform=Standardize(m=all_Y.shape[-1]).to(device=device, dtype=dtype),
    ).to(device=device, dtype=dtype)
    
    gp_model.likelihood.noise_covar.initialize(noise=0.01)
    mll = ExactMarginalLogLikelihood(gp_model.likelihood, gp_model).to(device=device, dtype=dtype)
    fit_gpytorch_mll(mll)
    
    t_train = time.perf_counter() - t0 # <--- GP TRAIN TIME

    # EHVI acquisition
    print("Computing Pareto frontier...")
    pareto_mask = is_non_dominated(all_Y)
    pareto_Y = all_Y[pareto_mask]
    print(f"Pareto frontier size: {pareto_Y.shape[0]}")
    
    partitioning = NondominatedPartitioning(ref_point=ref_point, Y=pareto_Y)
    EHVI = qLogExpectedHypervolumeImprovement(
        model=gp_model,
        ref_point=ref_point.tolist(),
        partitioning=partitioning,
    )
    
    # Optimize
    print("Optimizing acquisition function...")
    t0 = time.perf_counter()

    candidate, acq_value = optimize_acqf(
        acq_function=EHVI,
        bounds=bounds,
        q=1,
        num_restarts=10,
        raw_samples=100,
    )
    
    t_acq = time.perf_counter() - t0   # <--- ACQ OPT TIME


    next_X = candidate.detach()
    next_params = next_X.squeeze().detach().cpu().numpy()
    print(f"EHVI value: {acq_value:.6f}")
    
    c10, c12a, c12b, c21a, c21b, c23a, c23b = next_params
    
    # --- Hardware Query & Timing ---
    t0 = time.perf_counter()
    # Explicitly unpack 7 parameters
    c10, c12a, c12b, c21a, c21b, c23a, c23b = next_params
    
    t0 = time.perf_counter()

    print("Querying STEM simulator...")
    objective1, objective2, next_image = get_stem_image_contrast_and_fft(
        c10, c12a, c12b, c21a, c21b, c23a, c23b, plot_diagnostics=True
    )

    t_hw = time.perf_counter() - t0    # <--- HARDWARE TIME    

    next_Y = torch.tensor([[objective1, objective2]], dtype=dtype, device=device)
    print(f"Observed: Contrast={objective1:.4f}, FFT={objective2:.4f}")

    # --- Logging ---
    c10, c12a, c12b, c21a, c21b, c23a, c23b = next_params # <--- Unpacked immediately
    params = {
        'C10': c10, 'C12a': c12a, 'C12b': c12b,
        'C21a': c21a, 'C21b': c21b, 'C23a': c23a, 'C23b': c23b
    }
    t_step_total = time.perf_counter() - t_step_start # <--- TOTAL STEP TIME
    phase = "BO"

    save_step(exp_dir, history, step, phase, params, objective1, objective2, t_train, t_acq, t_hw, t_step_total, next_image)
    global_step += 1
    # Update tensors on-device
    all_X = torch.cat([all_X, next_X], dim=0)
    all_Y = torch.cat([all_Y, next_Y], dim=0)
    all_images.append(next_image)
    
    new_pareto_mask = is_non_dominated(all_Y)
    if new_pareto_mask[-1]:
        print("✓ NEW PARETO POINT!")


# %% [markdown]
# ### 2.4 Lets Visualize the Pareto-Frontier and see the MO-BO suggested solutions on it

# %%
# ========== FIND EXTREME AND MID PARETO POINTS (B2-A2 Version) ==========
print("\n" + "="*60)
print("Pareto Frontier Analysis")
print("="*60)

final_pareto_mask = is_non_dominated(all_Y)
final_pareto_X = all_X[final_pareto_mask]
final_pareto_Y = all_Y[final_pareto_mask]
pareto_indices = torch.where(final_pareto_mask)[0].cpu().numpy()

print(f"Number of Pareto optimal points: {final_pareto_Y.shape[0]}")

# Find extreme points
extreme_indices = []

# Extreme for Objective 1
max_obj1_idx = torch.argmax(final_pareto_Y[:, 0]).item()
min_obj1_idx = torch.argmin(final_pareto_Y[:, 0]).item()

# Extreme for Objective 2
max_obj2_idx = torch.argmax(final_pareto_Y[:, 1]).item()
min_obj2_idx = torch.argmin(final_pareto_Y[:, 1]).item()

extreme_indices.extend([max_obj1_idx, min_obj1_idx, max_obj2_idx, min_obj2_idx])
extreme_indices = list(set(extreme_indices))  # Remove duplicates

# Find middle point (balanced trade-off)
# Normalize objectives to [0,1] then find point closest to (0.5, 0.5)
normalized_pareto_Y = (final_pareto_Y - final_pareto_Y.min(dim=0).values) / (final_pareto_Y.max(dim=0).values - final_pareto_Y.min(dim=0).values + 1e-8)
distances_to_center = torch.norm(normalized_pareto_Y - 0.5, dim=1)
mid_idx = torch.argmin(distances_to_center).item()

# Combine: extremes + mid
selected_indices = sorted(list(set(extreme_indices + [mid_idx])))

print(f"\nSelected Pareto points for visualization: {len(selected_indices)}")
for idx in selected_indices:
    pareto_idx = pareto_indices[idx]
    params = all_X[pareto_idx].cpu().numpy()
    obj1, obj2 = all_Y[pareto_idx, 0].item(), all_Y[pareto_idx, 1].item()
    
    label = ""
    if idx == max_obj1_idx:
        label += "[MAX Obj1] "
    if idx == min_obj1_idx:
        label += "[MIN Obj1] "
    if idx == max_obj2_idx:
        label += "[MAX Obj2] "
    if idx == min_obj2_idx:
        label += "[MIN Obj2] "
    if idx == mid_idx:
        label += "[MID/Balanced] "
    
    print(f"  {label}")
    print(f"    Obj1={obj1:.4f}, Obj2={obj2:.4f}")
    # UPDATED: Print 4 parameters (C21, C23)
    print(f"    C21=({params[0]:.2f}, {params[1]:.2f})")
    print(f"    C23=({params[2]:.2f}, {params[3]:.2f})")

# ========== VISUALIZE ONLY EXTREME + MID PARETO IMAGES ==========
n_selected = len(selected_indices)
n_cols = min(3, n_selected)
n_rows = int(np.ceil(n_selected / n_cols))

fig = plt.figure(figsize=(7*n_cols, 7*n_rows))

for plot_idx, pareto_idx_in_frontier in enumerate(selected_indices):
    ax = fig.add_subplot(n_rows, n_cols, plot_idx + 1)
    
    pareto_idx = pareto_indices[pareto_idx_in_frontier]
    img = all_images[pareto_idx]
    params = all_X[pareto_idx].cpu().numpy()
    obj1, obj2 = all_Y[pareto_idx, 0].item(), all_Y[pareto_idx, 1].item()
    
    # Determine label
    label = ""
    if pareto_idx_in_frontier == max_obj1_idx:
        label = "MAX Obj1"
        color = 'red'
    elif pareto_idx_in_frontier == min_obj1_idx:
        label = "MIN Obj1"
        color = 'blue'
    elif pareto_idx_in_frontier == max_obj2_idx:
        label = "MAX Obj2"
        color = 'green'
    elif pareto_idx_in_frontier == min_obj2_idx:
        label = "MIN Obj2"
        color = 'orange'
    elif pareto_idx_in_frontier == mid_idx:
        label = "BALANCED (Mid)"
        color = 'purple'
    else:
        label = "Extreme"
        color = 'black'
    

    ax.imshow(np.array(img), cmap='gray')
    
    # UPDATED: Title includes 4 parameters (C21, C23)
    ax.set_title(
        f'{label}\n'
        f'Obj1={obj1:.4f}, Obj2={obj2:.4f}\n'
        f'C21=({params[0]:.1f}, {params[1]:.1f})\n'
        f'C23=({params[2]:.1f}, {params[3]:.1f})',
        fontsize=10,
        fontweight='bold',
        color=color
    )
    ax.axis('off')

plt.tight_layout()
plt.savefig(f'{exp_dir}/pareto_extreme_mid_images.png', dpi=150, bbox_inches='tight')
plt.show()

# ========== COMBINED RESULTS PLOT ==========
fig = plt.figure(figsize=(18, 6))

# 1. Objective space
ax1 = plt.subplot(1, 3, 1)
ax1.scatter(all_Y[:, 0].cpu().numpy(), all_Y[:, 1].cpu().numpy(), 
           c='lightblue', s=150, alpha=0.6, edgecolors='gray',
           label='All evaluations')
ax1.scatter(final_pareto_Y[:, 0].cpu().numpy(), final_pareto_Y[:, 1].cpu().numpy(), 
           c='lightcoral', s=200, alpha=0.5, edgecolors='black', 
           linewidths=1, label='Pareto frontier')

# Highlight extreme and mid points
colors = []
labels_legend = []
for idx in selected_indices:
    if idx == max_obj1_idx:
        colors.append('red')
        if 'MAX Obj1' not in labels_legend:
            labels_legend.append('MAX Obj1')
    elif idx == max_obj2_idx:
        colors.append('green')
        if 'MAX Obj2' not in labels_legend:
            labels_legend.append('MAX Obj2')
    elif idx == mid_idx:
        colors.append('purple')
        if 'Balanced' not in labels_legend:
            labels_legend.append('Balanced')
    else:
        colors.append('orange')

for idx, color in zip(selected_indices, colors):
    ax1.scatter(final_pareto_Y[idx, 0].cpu().numpy(), final_pareto_Y[idx, 1].cpu().numpy(),
               c=color, s=400, marker='*', edgecolors='black', linewidths=2, zorder=10)

ax1.set_xlabel('Objective 1 (Contrast)', fontsize=12)
ax1.set_ylabel('Objective 2 (FFT Score)', fontsize=12) # Fixed Label
ax1.set_title('Pareto Frontier (★ = Extreme/Mid points)', fontsize=14, fontweight='bold')
ax1.legend(fontsize=10)
ax1.grid(True, alpha=0.3)

# 2. BO progress
ax2 = plt.subplot(1, 3, 2)
iterations = range(len(all_Y))

ax2.plot(
    iterations,
    (all_Y[:, 0].cpu().numpy() - all_Y[:, 0].cpu().numpy().min()) /
    (all_Y[:, 0].cpu().numpy().max() - all_Y[:, 0].cpu().numpy().min() + 1e-12),
    'o-', label='Contrast', alpha=0.7, linewidth=2, markersize=8
)

ax2.plot(
    iterations,
    (all_Y[:, 1].cpu().numpy() - all_Y[:, 1].cpu().numpy().min()) /
    (all_Y[:, 1].cpu().numpy().max() - all_Y[:, 1].cpu().numpy().min() + 1e-12),
    's-', label='FFT Score', alpha=0.7, linewidth=2, markersize=8
)
# ax2.plot(iterations, all_Y[:, 0].cpu().numpy(), 'o-', label='Contrast', 
#         alpha=0.7, linewidth=2, markersize=8)
# ax2.plot(iterations, all_Y[:, 1].cpu().numpy(), 's-', label='FFT Score', 
#         alpha=0.7, linewidth=2, markersize=8)
ax2.axvline(len(train_Y)-1, color='red', linestyle='--', 
          label='BO start', linewidth=2)
ax2.set_xlabel('Iteration', fontsize=12)
ax2.set_ylabel('Objective Value', fontsize=12)
ax2.set_title('BO Progress', fontsize=14, fontweight='bold')
ax2.legend(fontsize=10)
ax2.grid(True, alpha=0.3)

# 3. Hypervolume
from botorch.utils.multi_objective.hypervolume import Hypervolume

# Ensure ref_point is on correct device if defined earlier, else re-define
if 'ref_point' not in locals():
    ref_point = train_Y.min(dim=0).values - 0.1 * train_Y.std(dim=0)

hv_computer = Hypervolume(ref_point=ref_point)
hypervolumes = []
for i in range(len(train_Y), len(all_Y) + 1):
    current_Y = all_Y[:i]
    pareto_mask_i = is_non_dominated(current_Y)
    pareto_Y_i = current_Y[pareto_mask_i]
    hv = hv_computer.compute(pareto_Y_i)
    hypervolumes.append(hv)

ax3 = plt.subplot(1, 3, 3)
ax3.plot(range(len(train_Y), len(all_Y) + 1), hypervolumes, 'o-', 
        linewidth=2, markersize=8, color='purple')
ax3.set_xlabel('Iteration', fontsize=12)
ax3.set_ylabel('Hypervolume', fontsize=12)
ax3.set_title('Hypervolume Improvement', fontsize=14, fontweight='bold')
ax3.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(f'{exp_dir}/multi_objective_summary.png', dpi=150, bbox_inches='tight')
plt.show()

print("\n=== SUMMARY ===")
print(f"Total evaluations: {len(all_Y)}")
print(f"Pareto frontier size: {len(pareto_indices)}")
print(f"Extreme + Mid points shown: {len(selected_indices)}")
print(f"Final hypervolume: {hypervolumes[-1]:.4f}")

# %%
# ========== COMPUTE PARETO FRONT ==========
final_pareto_mask = is_non_dominated(all_Y)
final_pareto_Y = all_Y[final_pareto_mask]
pareto_indices = torch.where(final_pareto_mask)[0].cpu().numpy()

print(f"Number of Pareto-optimal points: {len(pareto_indices)}")

# ========== PLOT ALL PARETO IMAGES ==========
n_pareto = len(pareto_indices)

# Layout
n_cols = min(5, n_pareto)     # adjust for image size
n_rows = int(np.ceil(n_pareto / n_cols))

fig = plt.figure(figsize=(4 * n_cols, 4 * n_rows))

for i, global_idx in enumerate(pareto_indices):
    ax = fig.add_subplot(n_rows, n_cols, i + 1)

    img = all_images[global_idx]
    params = all_X[global_idx].cpu().numpy()
    obj1, obj2 = all_Y[global_idx, 0].item(), all_Y[global_idx, 1].item()

    ax.imshow(np.array(img), cmap="gray")
    
    # Condensed title for 7 params
    ax.set_title(
        f"Con={obj1:.3f}, FFT={obj2:.3f}\n"
        f"C10={params[0]:.1f}, C12=({params[1]:.1f},{params[2]:.1f})\n"
        f"C21=({params[3]:.1f},{params[4]:.1f})\n"
        f"C23=({params[5]:.1f},{params[6]:.1f})",
        fontsize=8
    )
    ax.axis("off")

plt.tight_layout()
plt.savefig(f"{exp_dir}/pareto_all_images.png", dpi=150, bbox_inches="tight")
plt.show()


# %%



