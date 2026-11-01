# A Moving Grounding Line in E3SM

Creation date: 2026/11/01

Contributors:

- Xylar Asay-Davis
- Claude

## Summary

In the ISOMIP+ experiments Ocean3 and Ocean4, the ice shelf thins and
thickens over 100 years, and its grounding line retreats or advances. In
E3SM, MALI replays that geometry in data mode: it reads monthly thickness
records from a file that Polaris writes
([MISOMIP inputs design](misomip_e3sm_init.md)). MPAS-Ocean imports the
geometry through the coupler, and wetting and drying under a thin film lets
water enter and leave the cavity's grounded edge.

The first 4 km runs of Ocean3 and Ocean4 in E3SM, 2 months long,
completed. This design covers the changes that a moving grounding line
needs, all to E3SM on FAnSSIE, the E3SM development branch for
ocean–ice-sheet coupling:

- MALI's masks follow each new data-mode thickness record at once, and
  MALI no longer removes ice that a record advances;
- MPAS-Ocean's cap on the land-ice pressure under grounded ice is safe
  with threads;
- MPAS-Ocean uses one ocean density wherever it turns land-ice pressure
  into a depth, the density MALI uses for flotation;
- MPAS-Ocean's land-ice mask follows the imported ice fraction, so ice can
  float wherever it is;
- MPAS-Ocean's monthly means include the ocean's land-ice geometry, which
  now changes with time;
- MPAS-Ocean exchanges no melt or frazil with MALI where MALI has too
  little floating ice to receive it, and treats slivers of ice at the
  calving front as open ocean.

Polaris's inputs treat the same slivers as open ocean
([MISOMIP inputs design](misomip_e3sm_init.md)).
Otherwise, Polaris needs no change. Success means that 4 km Ocean3 and Ocean4
run in E3SM across several record changes and pass the checks under
[Testing](#testing).

## Requirements

### Requirement: The exported geometry is consistent

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

At every coupling, MALI's land-ice pressure, ice mask, floating mask and
surface elevation describe the same thickness. In data mode, that
thickness is the record in effect.

### Requirement: The ocean's ice mask follows the ice

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

Wherever the imported geometry has ice, MPAS-Ocean treats the cell as
covered by ice, and nowhere else. Cells that newly float
receive land-ice fluxes, and cells that newly ground stop receiving them.

### Requirement: One ocean density relates pressure and depth

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

MALI's flotation test and every MPAS-Ocean calculation that converts
land-ice pressure to a depth use the same ocean density.

### Requirement: The ocean's geometry is in its monthly means

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

When MPAS-Ocean is coupled to an ice sheet, its monthly means include the
land-ice pressure, draft, ice fraction and floating fractions.

### Requirement: Threaded runs match unthreaded ones

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

The grounded-ice pressure cap gives the same answers with any number of
threads.

### Requirement: Ice-shelf water reaches the ice sheet

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

Melt and frazil that leave the ocean under the ice shelf are the ice-shelf
exchange that MALI receives, so the coupler's budget for that exchange
closes.

## Algorithm Design

### Algorithm Design: The exported geometry is consistent

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

When data mode reads a new thickness record, MALI recomputes its masks and
its upper and lower surfaces from it before anything else uses them.

```{admonition} Rationale
MALI's E3SM driver reads time-varying input at the end of each time step,
after the step has computed the masks. A record that took effect on the
last step of a coupling interval was exported with the new thickness, so
the new land-ice pressure, but the old floating and grounded masks. In the
4 km Ocean3 run, 7 ocean cells had a floating fraction that lagged by up to
0.05 for one day. A restart on a record boundary recomputes the masks at
initialization, so it also differed from a continuous run.
```

Outside prognostic mode, MALI does not restore its calving front, so ice
that a record places beyond the initial front stays
([D5](#d5-the-calving-front-is-not-restored-outside-prognostic-mode)).

```{admonition} Rationale
MALI's E3SM default restores the calving front, removing floating ice that
advances past its initial extent. In the 4 km Ocean4 run, whose ice shelf
re-advances, the step after the March read removed 100 m of ice from one
MALI cell, and one ocean cell's floating fraction fell from 0.82 to 0.76
for the rest of the month.
```

### Algorithm Design: The ocean's ice mask follows the ice

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

Whenever MPAS-Ocean updates its land-ice fraction during a run, it sets its
land-ice mask to 1 where the fraction is positive and to 0 elsewhere
([D2](#d2-slivers-of-ice-are-open-ocean)). This
applies to the geometry imported from MALI and to standalone time-varying
land-ice forcing ([D3](#d3-standalone-land-ice-forcing-updates-the-mask-too)).
Wetting and drying then decides where masked cells float, as now: grounded
cells get a floating fraction of 0, and floating cells keep their input's
fraction. The first update reproduces the initial condition's mask, and
the mask is in the restart stream.

```{admonition} Rationale
The mask comes from the initial condition and never changes. Only masked
cells can float, and the vertical mixing, surface restoring, frazil and
wetting-and-drying code treat masked cells as ice-covered. Over 100 years
of the 4 km forcing, ice reaches up to 3 unmasked cells in Ocean3 and 21
in Ocean4, which then never float or melt. Ice leaves 38 and 45 masked
cells. These receive no land-ice fluxes, since fluxes scale with the
floating fraction, but are otherwise treated as ice-covered.
```

### Algorithm Design: Ice-shelf water reaches the ice sheet

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

MPAS-Ocean already sends MALI no ice-shelf flux in a cell where MALI's
floating fraction is at most `config_glc_ice_shelf_coupling_min_floating_fraction`
(1e-3). When coupled to MALI, it now forms no frazil under floating ice and
computes no melt in those cells, as under grounded ice
([D6](#d6-no-ice-shelf-exchange-where-mali-cannot-receive-it)). Imported
cells whose ice fraction is at most the same value are open ocean
([D2](#d2-slivers-of-ice-are-open-ocean)).

```{admonition} Rationale
In 4 km Ocean4, frazil formed in two calving-front cells holding slivers of
ice (fractions 1.8e-4 and 6.3e-5). The ocean booked it as leaving to MALI,
but sent none, so the exchange's daily budget missed 3–6 %. Grounding-line
cells with a sliver of floating ice can leak the same way.
```

### Algorithm Design: One ocean density relates pressure and depth

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

MPAS-Ocean's grounded-ice pressure cap and its wetting-and-drying
flotation depth use the land-ice ocean density, which already sets the
ice draft ([D1](#d1-the-land-ice-ocean-density-converts-pressure-to-depth)).
Polaris sets MALI's ocean density to the same value, 1028 kg m⁻³.

```{admonition} Rationale
The cap and the flotation depth use the ocean's reference density, 1026
kg m⁻³ in E3SM and by default standalone. The draft at the capped pressure
is then 0.2 % shallower than the cap intends, which leaves about 1 m more
water under grounded ice at 500 m depth than the thin film's critical
thickness.
```

### Algorithm Design: Threaded runs match unthreaded ones

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

The cap is applied in the same threaded loop that adds land-ice pressure
to the surface pressure, with no temporary array.

```{admonition} Rationale
The cap's loop opens a parallel region inside one that is already open. So
each thread computes the whole cap into a shared array while other threads
read it. The values agree, so answers have not differed, but the race is
undefined behavior and the work is repeated once per thread.
```

## Implementation

### Implementation: Branches and pull requests

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

Each change is a branch from FAnSSIE and a pull request into it, holding
only code:

| Branch | Change |
|---|---|
| `mali/update-masks-after-data-mode-read` | MALI recomputes its masks and surfaces after a data-mode read, and leaves its calving front free outside prognostic mode |
| `ocn/thread-safe-land-ice-pressure-cap` | the cap is threaded safely, and unused code around it goes; bit for bit |
| `ocn/land-ice-pressure-cap-ocean-density` | the cap and flotation depth use the land-ice ocean density; stacked on the previous branch |
| `ocn/land-ice-mask-from-fraction` | the land-ice mask follows the ice fraction |
| `ocn/land-ice-geometry-in-monthly-means` | the monthly means include the land-ice geometry |
| `ocn/skip-frazil-without-glc-floating-ice` | no frazil or melt where MALI's floating fraction is at most the sending threshold |
| `ocn/ignore-tiny-glc-ice-fractions` | imported slivers of ice are open ocean; stacked on the previous branch |

The cap came from
[E3SM-Ocean-Discussion#119](https://github.com/E3SM-Ocean-Discussion/E3SM/pull/119),
and its two fixes apply there too. The MALI change also suits MALI-Dev,
whose driver code it changes.

### Implementation: MALI's masks after a data-mode read

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

In `glc_run_mct`, the driver notes before each read whether the
`data-mode-input` stream's alarm is ringing. If it is, then after the read
it calls `li_calculate_mask` on each block, exchanges the cell, edge and
vertex mask halos, and calls `li_update_geometry`. That is the order MALI's
time integrator uses. History output, the export and the next time step
all see the new masks. Other input streams carry no thickness and change
nothing ([D4](#d4-masks-are-recomputed-only-after-a-data-mode-read)).

MALI's `build-namelist` sets `config_restore_calving_front` to false by
default in static and data modes, as it already does for calving, iceberg
removal and small-island removal. Static mode never changes thickness, so
its answers do not change.

### Implementation: The pressure cap

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

In `ocn_diagnostic_solve_surface_pressure`, the cap folds into the
`!$omp do` loop that adds land-ice pressure. The local
`landIcePressureApplied` array, the module pointer of the same name (which
no Registry field backs), `landIcePressureInitialized` and the misspelled
`M_OPENACC` guard go. A second commit removes two arrays that
`ocn_compute_land_ice_ssh_adjustment` allocates and never uses. The
stacked branch replaces `rho_sw` with `config_land_ice_rho_ocean` in the
cap and in `ocn_compute_land_ice_ssh_floatation`.

### Implementation: The land-ice mask

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

`ocn_import_glc_shelf_mct` sets `landIceMask` from `landIceFraction` when
it imports geometry, and exchanges its halo. The time-varying land-ice
forcing does the same where it sets `landIceFraction`. Both also update
the ideal-age forcing mask, the one field MPAS-Ocean derives from
`landIceMask` once at initialization. Every other user reads the mask when
it runs.

The coupler's ocean domain mask, which is 0 under land ice, also comes
from `landIceMask` at initialization and stays fixed for the run. In these
experiments every other component is a stub, so it matters only for the
coupler's budget of open-ocean frazil, which it weights by that mask.
Realistic coupling with a moving ice front would need a coupler change,
which this design does not cover.

### Implementation: The geometry in the monthly means

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

When `OCN_GLC_ISMF_COUPLING` is `coupler`, MPAS-Ocean's `buildnml` adds
`landIcePressure`, `landIceDraftForSsh`, `landIceFraction`,
`landIceFloatingFraction` and `landIceGlcFloatingFraction` to
`timeSeriesStatsMonthlyOutput`. `landIceDraftForSsh` is the draft computed
from the imported pressure; `landIceDraft` keeps the initial condition's
values in coupled runs. `landIceGlcFloatingFraction` is the ice sheet's
own remapped floating fraction, to compare with the ocean's. The monthly
means cannot include `landIceMask`, since only real fields are averaged. A
cell with a positive mean ice fraction had ice during the month.

## Testing

### Testing and Validation: Ocean3 and Ocean4 in E3SM

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

Each pull request is tested against a baseline from FAnSSIE's previous
commit:

- Polaris's `fanssie_pr` suite is bit for bit after the MALI change, the
  threading change and the mask change. After the density change, it
  changes only in the thin-film tasks, which the pull request lists, and
  the standalone thin-film ISOMIP+ tasks change too.
- After the mask change, the mask after the first update equals the
  initial condition's, in E3SM and standalone.
- After the monthly-means change, an E3SM Ocean3 run's monthly means hold
  the five fields, and nothing else in the run changes.
- After the MALI change, 4 km Ocean3 and Ocean4 in E3SM are bit for bit
  with the baseline until the first record change. From then on, the
  imported ice and floating fractions match the data-mode record at every
  coupling, and MALI's thickness in its history matches the record cell by
  cell.
- The threading change runs 4 km Ocean3 in E3SM for 5 days with 2 threads
  per ocean task and without threads, and the two agree bit for bit.

With all of these changes, 4 km Ocean3 and Ocean4 run in E3SM with RK4 for a
length agreed beforehand that spans several record changes. They are
checked for the following:

- the geometry the ocean imports tracks the data-mode file;
- the grounding line moves without failure;
- the open-ocean SSH stays near zero;
- no cell without ice receives land-ice fluxes;
- from the second day, the coupler's daily budget for the ice-shelf water
  exchange closes.

## Decisions

### D1: The land-ice ocean density converts pressure to depth

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

The grounded-ice pressure cap and the wetting-and-drying flotation depth
use `config_land_ice_rho_ocean`, as the draft does:

- the flotation depth runs only with wetting and drying, so it changes
  the answers of every run with a thin film;
- the cap runs in every run with land-ice pressure. With this change, it
  binds in a subset of the cells it binds in now, those where the ice is
  too heavy to float above the bed;
- E3SM master has no cap, so nothing there changes.

Rejected:

- the ocean's reference density everywhere, including the draft. The
  draft would then disagree with MALI's flotation test, which sets the
  grounding line;
- the new density in the flotation depth only. The cap would still miss
  the draft it is meant to allow;
- applying the cap only with wetting and drying. That changes when the cap
  applies, which belongs to the cap's own design.

### D2: Slivers of ice are open ocean

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

The mask is 1 wherever the ice fraction is positive. When MPAS-Ocean
imports MALI's geometry, it first sets the pressure and fractions to 0
where the ice fraction is at most
`config_glc_ice_shelf_coupling_min_floating_fraction` (1e-3), the threshold
below which it already sends MALI nothing. Polaris's initial conditions
and standalone forcing apply the same threshold, so the first update
reproduces the initial mask and the coupler's open-ocean domain includes
those cells. The ice fraction already includes grounded ice, and wetting
and drying decides which masked cells float.

Rejected:

- keeping the initial mask and letting only the floating fraction change.
  Cells that gain ice could never float, and cells that lose it would
  still be treated as ice-covered;
- a mask wherever the fraction is positive. Slivers at the calving front
  would be ice-covered cells that cannot exchange fluxes with MALI;
- a new threshold option. The existing sending threshold is the point
  below which a cell cannot exchange fluxes with MALI.

### D3: Standalone land-ice forcing updates the mask too

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

The time-varying land-ice forcing sets the mask by the same rule as the
import. Standalone Ocean3 and Ocean4 then treat a moving ice front as
E3SM does, and their answers change where the ice front moves.

Rejected: changing only E3SM's import, which would make standalone and
E3SM Ocean3–4 differ in how they treat the cells at a moving ice front.

### D4: Masks are recomputed only after a data-mode read

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

MALI recomputes its masks and surfaces after a read only if the read
included the data-mode stream.

Rejected: recomputing them after any input read. In prognostic runs, the
start of the next time step uses the masks before recomputing them, so
prognostic answers would change.

### D5: The calving front is not restored outside prognostic mode

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

MALI's `build-namelist` turns off calving-front restoration by default in
static and data modes, where MALI should replay or hold its thickness
unchanged.

Rejected: setting it in the Ocean3 and Ocean4 case configurations. That
fixes only these cases and leaves other data-mode runs with the same
problem.

### D6: No ice-shelf exchange where MALI cannot receive it

Date last modified: 2026/11/01

Contributors: Xylar Asay-Davis, Claude

When coupled to MALI, MPAS-Ocean forms no frazil under floating ice and
computes no melt where MALI's floating fraction is at most the sending
threshold. Elsewhere, frazil forms as before, under ice shelves and in the
open ocean, where the coupler books it as freezing with no receiver.

Rejected:

- turning off frazil under ice shelves. Frazil there is part of the
  exchange with MALI wherever MALI can receive it;
- reporting the unsent flux to the coupler in a new budget row. The row
  would change the size of the budget sums in the coupler's restart files.
