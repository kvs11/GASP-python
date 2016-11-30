# coding: utf-8
# Copywrite (c) Henniggroup.
# Distributed under the terms of the MIT License.

from __future__ import division, unicode_literals, print_function

"""
General module:

This module contains several classes central to the algorithm.

1. IDGenerator: generates consecutive integer ID numbers for organisms

2. Organism: represents an organism, which consists primarily of a
         structure and an energy

3. InitialPopulation: represents the initial population of organisms,
         produced from one or more of the organism creators

4. Pool: represents the population of organisms, after the initial population

5. OffspringGenerator: high-level class for making offspring organisms

6. SelectionProbDist: specifies the distribution from which selection
        probabilities are drawn

7. CompositionSpace: specifies the composition or composition range

8. StoppingCriteria: specifies when a search should stop

"""

from pymatgen.core.structure import Structure
from pymatgen.core.lattice import Lattice
from pymatgen.core.composition import Composition
from pymatgen.core.periodic_table import Element
from pymatgen.phasediagram.maker import CompoundPhaseDiagram
from pymatgen.phasediagram.entries import PDEntry
from pymatgen.phasediagram.analyzer import PDAnalyzer
from pymatgen.transformations.standard_transformations import \
    RotationTransformation

import os
import copy
import math
import numpy as np
from _pyio import __metaclass__
from collections import deque
from scipy.spatial.qhull import ConvexHull


class IDGenerator(object):
    """
    Generates successive integer ID numbers, starting from 1.

    This class is a singleton.
    """

    def __init__(self):
        """
        Creates an id generator.
        """

        self.id = 0

    def make_id(self):
        """
        Returns the next id number.
        """

        self.id += 1
        return self.id


class Organism(object):
    """
    An organism
    """

    def __init__(self, structure, id_generator, maker):
        """
        Creates an organism

        Args:
            structure: The structure of this organism, as a
                pymatgen.core.structure.Structure

            id_generator: the instance of IDGenerator used to assign id numbers
                to all organisms

            maker: the name of algorithm that made the organism, as a string.
                Either a creator or a variation.
        """

        self.structure = structure
        self.composition = self.structure.composition
        self.total_energy = None
        self.epa = None
        # objective function value
        self.value = None
        self.fitness = None
        # selection probability of this organism
        self.selection_prob = None
        # starting point of interval on number line of size selection_prob
        self.selection_loc = None
        # whether the organism is part of initial population or pool
        self.is_active = False
        # unique id number
        self._id = id_generator.make_id()
        # the name of the algorithm that created this organism
        self.made_by = maker

    # This keeps the id (sort of) immutable by causing an exception to be
    # raised if the user tries to the set the id with org.id = some_id.
    @property
    def id(self):
        return self._id

    def rotate_to_principal_directions(self):
        """
        Rotates the organism's structure into the principal directions. That
        is, a is parallel to the Cartesian x-axis, b lies in the Cartesian x-y
        plane and the z-component of c is positive.

        Note: this method doesn't change the fractional coordinates of the
        sites. However, the Cartesian coordinates may be changed.
        """

        # rotate about the z-axis to align a vertically with the x-axis
        rotation = RotationTransformation(
            [0, 0, 1], 180 - (180/np.pi)*np.arctan2(
                    self.structure.lattice.matrix[0][1],
                    self.structure.lattice.matrix[0][0]))
        self.structure = rotation.apply_transformation(self.structure)
        # rotate about the y-axis to make a parallel to the x-axis
        rotation = RotationTransformation(
                [0, 1, 0], (180/np.pi)*np.arctan2(
                    self.structure.lattice.matrix[0][2],
                    self.structure.lattice.matrix[0][0]))
        self.structure = rotation.apply_transformation(self.structure)
        # rotate about the x-axis to make b lie in the x-y plane
        rotation = RotationTransformation(
                [1, 0, 0], 180 - (180/np.pi)*np.arctan2(
                    self.structure.lattice.matrix[1][2],
                    self.structure.lattice.matrix[1][1]))
        self.structure = rotation.apply_transformation(self.structure)
        # rake sure they are all pointing in positive directions
        if self.structure.lattice.matrix[0][0] < 0:
            # rotate about y-axis to make a positive
            rotation = RotationTransformation([0, 1, 0], 180)
            self.structure = rotation.apply_transformation(self.structure)
        if self.structure.lattice.matrix[1][1] < 0:
            # rotate about x-axis to make b positive
            rotation = RotationTransformation([1, 0, 0], 180)
            self.structure = rotation.apply_transformation(self.structure)
        if self.structure.lattice.matrix[2][2] < 0:
            # mirror c across the x-y plane to make it positive
            # the components of a
            ax = self.structure.lattice.matrix[0][0]
            ay = self.structure.lattice.matrix[0][1]
            az = self.structure.lattice.matrix[0][2]
            # the components of b
            bx = self.structure.lattice.matrix[1][0]
            by = self.structure.lattice.matrix[1][1]
            bz = self.structure.lattice.matrix[1][2]
            # the components of c
            cx = self.structure.lattice.matrix[2][0]
            cy = self.structure.lattice.matrix[2][1]
            cz = -1*self.structure.lattice.matrix[2][2]

            self.structure.modify_lattice(Lattice([[ax, ay, az], [bx, by, bz],
                                                   [cx, cy, cz]]))

    def rotate_c_parallel_to_z(self):
        """
        Rotates the organism's structure such that the c lattice vector is
        parallel to the z axis.

        Note: this method doesn't change the fractional coordinates of the
        sites. However, the Cartesian coordinates may be changed.
        """

        # rotate about the z-axis until c lies in the x-z plane
        rotation = RotationTransformation(
                [0, 0, 1], 180 - (180/np.pi)*np.arctan2(
                    self.structure.lattice.matrix[2][1],
                    self.structure.lattice.matrix[2][0]))
        self.structure = rotation.apply_transformation(self.structure)
        # rotate about the y-axis to make c parallel to the z-axis
        rotation = RotationTransformation(
            [0, 1, 0], 180 - (180/np.pi)*np.arctan2(
                self.structure.lattice.matrix[2][0],
                self.structure.lattice.matrix[2][2]))
        self.structure = rotation.apply_transformation(self.structure)
        # make sure c is pointing along the positive z-axis
        if self.structure.lattice.matrix[2][2] < 0:
            # rotate 180 degrees about the x-axis
            rotation = RotationTransformation([1, 0, 0], 180)
            self.structure = rotation.apply_transformation(self.structure)

    def translate_atoms_into_cell(self):
        """
        Translates all the atoms into the cell, so that their fractional
        coordinates are between 0 and 1.

        Precondition: all the atoms can fit inside the structure's lattice
        """

        # get the bounding box of the atoms, in fractional coordinates
        bounding_box = self.get_bounding_box(cart_coords=False)

        # the translation vector, in fractional coordinates
        translation_vector = []

        # determine the needed shift along each lattice vector
        for i in range(3):
            if bounding_box[i][0] < 0.0:
                translation_vector.append(-1*(bounding_box[i][0]) + 0.001)
            elif bounding_box[i][1] > 1.0:
                translation_vector.append(-1*(bounding_box[i][1] + 0.001) +
                                          1.0)
            else:
                translation_vector.append(0.0)

        # do the shift
        site_indices = [i for i in range(len(self.structure.sites))]
        self.structure.translate_sites(site_indices, translation_vector,
                                       frac_coords=True, to_unit_cell=False)

    def reduce_sheet_cell(self):
        """
        Applies Niggli cell reduction to a sheet structure.

        The idea is to make c vertical and add lots of vertical vacuum so that
        the standard reduction algorithm only changes the a and b lattice
        vectors.

        TODO: pymatgen's Niggli cell reduction algorithm sometimes moves the
        atoms' relative positions a little (I've seen up to 0.5 A...).
        """

        # rotate into principal directions
        self.rotate_to_principal_directions()
        # get the species and their Cartesian coordinates
        species = self.structure.species
        cartesian_coords = self.structure.cart_coords
        # get the non-zero components of the a and b lattice vectors, and the
        # vertical component of the c lattice vector
        ax = self.structure.lattice.matrix[0][0]
        bx = self.structure.lattice.matrix[1][0]
        by = self.structure.lattice.matrix[1][1]
        cz = self.structure.lattice.matrix[2][2]
        # make a new lattice with a ton of vertical vacuum (add 100 Angstroms)
        padded_lattice = Lattice([[ax, 0.0, 0.0], [bx, by, 0.0],
                                  [0.0, 0.0, cz + 100]])
        # make a new structure with the padded lattice and Cartesian coords
        padded_structure = Structure(padded_lattice, species, cartesian_coords,
                                     coords_are_cartesian=True)
        # do cell reduction on the padded structure
        reduced_structure = padded_structure.get_reduced_structure()
        # unpad the reduced structure
        rspecies = reduced_structure.species
        rcartesian_coords = reduced_structure.cart_coords
        rax = reduced_structure.lattice.matrix[0][0]
        ray = reduced_structure.lattice.matrix[0][1]
        rbx = reduced_structure.lattice.matrix[1][0]
        rby = reduced_structure.lattice.matrix[1][1]
        unpadded_lattice = Lattice([[rax, ray, 0.0], [rbx, rby, 0.0],
                                    [0.0, 0.0, cz]])
        # set the organism's structure to the unpadded one
        self.structure = Structure(unpadded_lattice, rspecies,
                                   rcartesian_coords,
                                   coords_are_cartesian=True)
        # make sure the atoms are located inside the cell
        self.translate_atoms_into_cell()
        # slightly shift the atoms vertically so they lie in the (vertical)
        # center of the cell
        frac_bounds = self.get_bounding_box(cart_coords=False)
        z_center = frac_bounds[2][0] + (frac_bounds[2][1] -
                                        frac_bounds[2][0])/2
        translation_vector = [0, 0, 0.5 - z_center]
        site_indices = [i for i in range(len(self.structure.sites))]
        self.structure.translate_sites(site_indices, translation_vector,
                                       frac_coords=True, to_unit_cell=False)

    def get_bounding_box(self, cart_coords=True):
        """
        Returns the smallest and largest coordinates in each dimension of all
        the sites in the organism's structure.

        Args:
            frac_coords: whether to give the result in Cartesian or fractional
                coordinates

        TODO: maybe there's a cleaner way to do this...
        """

        # get coordinates of the atoms in the structure
        if cart_coords:
            coords = self.structure.cart_coords
        else:
            coords = self.structure.frac_coords
        # find the largest and smallest coordinates in each dimension
        minx = np.inf
        maxx = -np.inf
        miny = np.inf
        maxy = -np.inf
        minz = np.inf
        maxz = -np.inf
        for coord in coords:
            if coord[0] < minx:
                minx = coord[0]
            if coord[0] > maxx:
                maxx = coord[0]
            if coord[1] < miny:
                miny = coord[1]
            if coord[1] > maxy:
                maxy = coord[1]
            if coord[2] < minz:
                minz = coord[2]
            if coord[2] > maxz:
                maxz = coord[2]
        bounds = [[minx, maxx], [miny, maxy], [minz, maxz]]
        return bounds


class InitialPopulation():
    """
    The initial population of organisms
    """

    def __init__(self, run_dir_name):
        """
        Creates an initial population

        Args:
            run_dir_name: the name (not path) of the garun directory where the
                search will be done
        """

        self.run_dir_name = run_dir_name
        self.initial_population = []

    def add_organism(self, organism_to_add, composition_space):
        """
        Adds a relaxed organism to the initial population and updates
        whole_pop.

        Args:
            organism_to_add: the organism to add to the pool
        """

        # sort the sites of the organism to add
        organism_to_add.structure.sort()

        # write the organism we're adding to a poscar file
        organism_to_add.structure.to('poscar', os.getcwd() + '/POSCAR.' +
                                     str(organism_to_add.id))

        print('Adding organism {} to the initial population'.format(
            organism_to_add.id))

        # print out the composition and total energy (needed for making phase
        # diagram plots and tracking number of atoms in cell)
        print('Organism {} has composition {} and total energy {}'.format(
            organism_to_add.id,
            organism_to_add.composition.formula.replace(' ', ''),
            organism_to_add.total_energy))
        self.initial_population.append(organism_to_add)
        organism_to_add.is_active = True

    def replace_organism(self, old_org, new_org, composition_space):
        """
        Replaces an organism in the initial population with a new organism.

        Precondition: the old_org is a current member of the initial population

        TODO: instead of finding the old organism in the initial population by
        searching for the right id, it might better to eventually implement
        a __eq__(self, other) method in the Organism class to determine when
        two organisms are the same...

        Args:
            old_org: the organism in the initial population to replace
            new_org: the new organism to replace the old one
        """

        # sort the sites of the new organism
        new_org.structure.sort()

        # write the new organism to a poscar file
        new_org.structure.to('poscar', os.getcwd() + '/POSCAR.' +
                             str(new_org.id))

        print('Replacing organism {} with organism {} in the initial '
              'population'.format(old_org.id, new_org.id))
        # print out the composition and total energy (needed for making phase
        # diagram plots and tracking number of atoms in cell)
        print('Organism {} has composition {} and total energy {}'.format(
            new_org.id, new_org.composition.formula.replace(' ', ''),
            new_org.total_energy))

        # find the organism in self.initial_population with the same id as
        # old_org
        for org in self.initial_population:
            if org.id == old_org.id:
                self.initial_population.remove(org)
                org.is_active = False
        # add the new organism to the initial population
        self.initial_population.append(new_org)
        new_org.is_active = True

    def print_progress(self, composition_space):
        """
        Prints out either the best organism (for epa search) or the area/volume
        of the convex hull (for pd search).

        Args:
            composition_space: the CompositionSpace object
        """

        if composition_space.objective_function == 'epa':
            self.print_best_org()
        elif composition_space.objective_function == 'pd':
            self.print_convex_hull_area(composition_space)

    def print_best_org(self):
        """
        Prints out the value and number of the best organism in the initial
        population.
        """

        # sort the organisms in order of increasing epa
        sorted_list = copy.deepcopy(self.initial_population)
        sorted_list.sort(key=lambda x: x.epa, reverse=False)
        # print out the best one
        print('Organism {} is the best and has value {} eV/atom'.format(
            sorted_list[0].id, sorted_list[0].epa))

    def print_convex_hull_area(self, composition_space):
        """
        Computes and prints the area/volume of the current best convex hull.

        Args:
            composition_space: the CompositionSpace object
        """

        # check if the initial population contains organisms at all the
        # endpoints of the composition space
        if self.has_endpoints(composition_space) and \
                self.has_non_endpoint(composition_space):
            # TODO: below here is copied from pool.print_convex_hull_area
            # compute and print the area or volume of the convex hull
            pdentries = []
            for organism in self.initial_population:
                pdentries.append(PDEntry(organism.composition,
                                         organism.total_energy))
            compound_pd = CompoundPhaseDiagram(pdentries,
                                               composition_space.endpoints)
            # get the data for the convex hull
            qhull_data = compound_pd.qhull_data

            # for some reason, the last point is positive, so remove it
            hull_data = np.delete(qhull_data, -1, 0)

            # make a ConvexHull object from the hull data
            # Sometime this fails, saying that only two points were given to
            # construct the convex hull, even though the if statement above
            # checks that there are enough points.
            try:
                convex_hull = ConvexHull(hull_data)
            except:
                return
            # compute the volume or area
            if len(composition_space.endpoints) == 2:
                print('Area of the convex hull: {} '.format(convex_hull.area))
            else:
                print('Volume of the convex hull: {} '.format(
                    convex_hull.volume))

    def has_endpoints(self, composition_space):
        """
        Checks if there are organisms in the initial population at all of
        endpoints of the composition space.

        Returns a boolean indicating whether or not there are organisms at all
        the endpoints.

        Args:
            composition_space: the CompositionSpace object
        """

        for endpoint in composition_space.endpoints:
            has_endpoint = False
            for organism in self.initial_population:
                if endpoint.almost_equals(
                        organism.composition.reduced_composition):
                    has_endpoint = True
            # if one of the endpoints is missing from the initial population,
            # then the answer is False
            if not has_endpoint:
                return False
        # if we're here, then the initial population has all the endpoints
        return True

    def has_non_endpoint(self, composition_space):
        """
        Checks that the initial population contains at least one organism not
        at one of the endpoint compositions.

        Returns a boolean indicating whether or not there is an organism not
        at one of the endpoints.

        Args:
            composition_space: the CompositionSpace object
        """

        for organism in self.initial_population:
            not_endpoint = True
            for endpoint in composition_space.endpoints:
                if endpoint.almost_equals(
                        organism.composition.reduced_composition):
                    not_endpoint = False
            # if this organism is not equal to any of the endpoints, then the
            # answer is True
            if not_endpoint:
                return True
        # if we made it this far, then there is not an organism that is not at
        # one of the endpoints
        return False


class Pool(object):
    """
    Pool to hold all the organisms that are currently candidates for selection
    to become parents.

    Composed of two parts: a promotion set containing the best few organisms,
    and a queue containing the rest of the organisms in the pool.
    """
    def __init__(self, pool_params, composition_space, run_dir_name):
        """
        Creates a pool of organisms

        Args:
            pool_params: the parameters of the pool, as a dictionary

            composition_space: the CompositionSpace object

            run_dir_name: the name (not path) of the garun directory
        """

        # the default values
        if len(composition_space.endpoints) == 1:
            # then we're doing a fixed composition search
            self.default_size = 20
        elif len(composition_space.endpoints) == 2:
            # then we're doing a binary phase diagram search search
            self.default_size = 25
        elif len(composition_space.endpoints) == 3:
            # then we're doing a ternary phase diagram search
            self.default_size = 75
        elif len(composition_space.endpoints) == 4:
            # then we're doing a quaternary phase diagram search
            self.default_size = 150

        # this only gets used for fixed composition searches
        self.default_num_promoted = 3

        # if entire Pool block was set to default or left blank
        if pool_params in (None, 'default'):
            self.size = self.default_size
            self.num_promoted = self.default_num_promoted
        else:
            # check if size parameter was used
            if 'size' not in pool_params:
                self.size = self.default_size
            elif pool_params['size'] in (None, 'default'):
                self.size = self.default_size
            else:
                self.size = pool_params['size']

            # check if num_promoted parameter was used
            if 'num_promoted' not in pool_params:
                self.num_promoted = self.default_num_promoted
            elif pool_params['num_promoted'] in (None, 'default'):
                self.num_promoted = self.default_num_promoted
            else:
                self.num_promoted = pool_params['num_promoted']

        # list to hold the best few organisms in the pool
        self.promotion_set = []
        # deque containing the rest of the organisms in the pool
        self.queue = deque()
        # list to hold the parameters for the selection distribution
        self.selection = []
        # the number of organisms added to the pool (excluding the initial
        # population)
        self.num_adds = 0
        # the name (not  path) of the garun directory
        self.run_dir_name = run_dir_name

    def add_initial_population(self, initial_population, composition_space):
        """
        Adds the organisms of the initial population to the pool.

        Args:
            initial_population: an InitialPopulation object containing the
                relaxed organisms of the initial population

            composition_space: the CompositionSpace object

        Precondition: the pool is empty (no organisms have been previously
            added to it)
        """

        print('Populating the pool with the initial population...')

        # get the list of organisms in the initial population
        organisms_list = initial_population.initial_population

        # populate the promotion set and queue
        if composition_space.objective_function == 'epa':
            # set the values of the initial population organisms to their epa's
            for organism in organisms_list:
                organism.value = organism.epa

            # sort the list of initial population organisms in order of
            # increasing value
            organisms_list.sort(key=lambda x: x.value, reverse=False)

            # assign them to either the promotion set or the queue
            for i in range(len(organisms_list)):
                if i < self.num_promoted:
                    self.promotion_set.append(organisms_list[i])
                else:
                    self.queue.appendleft(organisms_list[i])

        elif composition_space.objective_function == 'pd':
            # Set the values of the initial population organisms to their
            # distances from the convex hull, and get the resulting compound
            # phase diagram object.
            try:
                self.compute_pd_values(organisms_list, composition_space)
            except:
                print('Error: could not construct the phase diagram because '
                      'there is not a structure at one or more of the '
                      'composition space endpoints.')
                print('One of the provided reference structures probably '
                      'failed development, or else there was an error when '
                      'calculating its energy. See previous output.')
                print('Quitting...')
                quit()

            # assign the ones on the convex hull to the promotion set and the
            # rest to the queue
            for organism in organisms_list:
                if organism.value < 0.000000001:
                    self.promotion_set.append(organism)
                else:
                    self.queue.appendleft(organism)

        # compute the fitnesses of the organisms in the initial population
        self.compute_fitnesses()

        # compute the selection probabilities of all the organisms in the
        # initial population
        self.compute_selection_probs()

        # sort the organisms in order of decreasing fitness
        organisms_list.sort(key=lambda x: x.fitness, reverse=True)

        # print out some info on the organisms in the initial population
        print('Summary of the initial population: ')
        for organism in organisms_list:
            print('Organism {} has value {} and fitness {} and selection '
                  'probability {} '.format(organism.id, organism.value,
                                           organism.fitness,
                                           organism.selection_prob))

    def add_organism(self, organism_to_add, composition_space):
        """
        Adds a new organism to the pool.

        For epa, if the new organism better than one of the organisms currently
        in the promotion set, then it is added to the promotion set, and the
        worst organism in the promotion set is moved to the back of the queue.
        Otherwise, the new organism is just appended to the back of the queue.

        For pd, if the new organism is on the convex hull, then it is added to
        the promotion set and any organisms in the promotion set that are no
        longer on the convex hull are moved the queue. Otherwise, the new
        organism is added to the back of the queue.

        Args:
            organism: the Organism to add to the pool

            composition_space: the CompositionSpace object
        """

        print('Adding organism {} to the pool '.format(organism_to_add.id))
        # print out the composition and total energy (needed for making phase
        # diagram plots and tracking number of atoms in cell)
        print('Organism {} has composition {} and total energy {} '.format(
            organism_to_add.id,
            organism_to_add.composition.formula.replace(' ', ''),
            organism_to_add.total_energy))

        # increment the number of adds to the pool
        self.num_adds = self.num_adds + 1

        # sort the sites of the organism to add
        organism_to_add.structure.sort()

        # write the organism we're adding to a poscar file
        organism_to_add.structure.to('poscar', os.getcwd() + '/POSCAR.' +
                                     str(organism_to_add.id))

        # the organism that we're adding to the pool is now active
        organism_to_add.is_active = True

        if composition_space.objective_function == 'epa':
            # set the value of the new organism to its epa
            organism_to_add.value = organism_to_add.epa
            # check if the new organism's value is better than the worst one
            # in the promotion set
            worst_organism = self.get_worst_in_promotion_set()
            if organism_to_add.value < worst_organism.value:
                # add the new organism to the promotion set
                self.promotion_set.append(organism_to_add)
                # move the worst organism from the promotion set to the back
                # (left end) of the queue
                self.promotion_set.remove(worst_organism)
                self.queue.appendleft(worst_organism)
            else:
                # append the new organism to the back (left end) of the queue
                self.queue.appendleft(organism_to_add)

        elif composition_space.objective_function == 'pd':
            # set the value of the new organism and update the values of all
            # the organisms in the pool
            organisms_list = self.to_list()
            organisms_list.append(organism_to_add)
            self.compute_pd_values(organisms_list, composition_space)

            # check if any of the organisms in the promotion set are no longer
            # on the convex hull
            self.check_promotion_set_pd()

            # add the new organism to the promotion set or the queue
            if organism_to_add.value == 0.0:
                self.promotion_set.append(organism_to_add)
            else:
                self.queue.appendleft(organism_to_add)

    def get_worst_in_promotion_set(self):
        """
        Returns the organism in the promotion set with the worst (largest)
        objective function value.
        """

        worst_organism = self.promotion_set[0]
        for organism in self.promotion_set:
            if organism.value > worst_organism.value:
                worst_organism = organism
        return worst_organism

    def check_promotion_set_pd(self):
        """
        For pd searches, checks whether promotion set and queue memberships are
        correct (all organisms with value 0 in promotion set, all organisms
        with value > 0 in queue), and moves organisms from promotion set to the
        back (left end) of the queue if needed.

        Precondition: a pd search is being done, and all organisms in the pool
            have up-to-date values (set by calling compute_pd_values)
        """

        # get a list of organisms in the promotion set that are no longer on
        # the convex hull
        organisms_to_demote = []
        for organism in self.promotion_set:
            if organism.value > 0.0:
                organisms_to_demote.append(organism)
        # move all the organism that are no longer on the convex hull from the
        # promotion set to the queue
        for organism_to_demote in organisms_to_demote:
            self.promotion_set.remove(organism_to_demote)
            self.queue.appendleft(organism_to_demote)

        # get a list of organisms in the queue that are now on the convex hull
        organisms_to_promote = []
        for organism in self.queue:
            if organism.value < 0.000000001:
                organisms_to_promote.append(organism)
        # move all the organisms on the convex hull to the promotion set
        for organism_to_promote in organisms_to_promote:
            self.queue.remove(organism_to_promote)
            self.promotion_set.append(organism_to_promote)

    def replace_organism(self, old_org, new_org, composition_space):
        """
        Replaces an organism in the pool with a new organism. The new organism
        has the same location in the pool as the old one.

        Precondition: the old_org is a member of the current pool.

        Args:
            old_org: the organism in the pool to replace

            new_org: the new organism to replace the old one
        """

        print('Replacing organism {} with organism {} in the pool '.format(
            old_org.id, new_org.id))
        # print out the composition and total energy (needed for making phase
        # diagram plots and tracking number of atoms in cell)
        print('Organism {} has composition {} and total energy {} '.format(
            new_org.id, new_org.composition.formula.replace(' ', ''),
            new_org.total_energy))

        # sort the sites of the new organism
        new_org.structure.sort()

        # write the new organism to a poscar file
        new_org.structure.to('poscar', os.getcwd() + '/POSCAR.' +
                             str(new_org.id))

        # compute the value of the new organism, and also of all the organisms
        # in the pool in pd search
        if composition_space.objective_function == 'epa':
            new_org.value = new_org.epa
        elif composition_space.objective_function == 'pd':
            organisms_list = self.to_list()
            organisms_list.append(new_org)
            self.compute_pd_values(organisms_list, composition_space)

        if old_org in self.promotion_set:
            self.promotion_set.remove(old_org)
            self.promotion_set.append(new_org)
        elif old_org in self.queue:
            # cast the queue to a list to do the replacement
            queue_list = list(self.queue)
            # insert the new organism next to the old organism in the list
            queue_list.insert(queue_list.index(old_org), new_org)
            # remove the old organism from the list
            queue_list.remove(old_org)
            # cast it back to a deque
            self.queue = deque(queue_list)

        # update is_active flags on both organisms
        old_org.is_active = False
        new_org.is_active = True

        # for pd searches, make sure promotion set and queue memberships are
        # correct in light of the new organism
        if composition_space.objective_function == 'pd':
            self.check_promotion_set_pd()

    def compute_pd_values(self, organisms_list, composition_space):
        """
        Constructs a convex hull from the provided organisms, and sets the
        organisms' values to their distances from the convex hull.

        Returns the compound phase diagram object computed from the organisms
        in organisms_list.

        Args:
            organisms_list: a list of Organisms whose values we need to compute

            composition_space: the CompositionSpace object
        """

        # create a PDEntry object for each organism in the list of organisms
        pdentries = {}
        for organism in organisms_list:
            pdentries[organism.id] = PDEntry(organism.composition,
                                             organism.total_energy)

        # put the pdentries in a list
        pdentries_list = []
        for organism_id in pdentries:
            pdentries_list.append(pdentries[organism_id])

        # create a compound phase diagram object from the list of pdentries
        compound_pd = CompoundPhaseDiagram(pdentries_list,
                                           composition_space.endpoints)

        # create a pd analyzer object from the compound phase diagram
        pd_analyzer = PDAnalyzer(compound_pd)

        # transform the pdentries and put them in a dictionary, with the
        # organism id's as the keys
        transformed_pdentries = {}
        for org_id in pdentries:
            transformed_pdentries[org_id] = compound_pd.transform_entries(
                    [pdentries[org_id]], composition_space.endpoints)[0][0]

        # put the values in a dictionary, with the organism id's as the keys
        values = {}
        for org_id in pdentries:
            values[org_id] = pd_analyzer.get_e_above_hull(
                transformed_pdentries[org_id])

        # assign values to the organisms
        for organism in organisms_list:
            organism.value = values[organism.id]

        # return the compound phase diagram
        return compound_pd

    def compute_fitnesses(self):
        """
        Calculates and assigns fitnesses to all organisms in the pool. If
        selection.num_parents is less than pool.size, then the fitnesses are
        computed relative to the selection.num_parents + 1 best organisms in
        the pool.

        Precondition: the organisms in the pool all have up-to-date values
        """

        # get the best organisms to act as parents
        if self.selection.num_parents < self.size:
            # get the best selection.num_parents + 1 organisms
            best_organisms = self.get_n_best_organisms(
                self.selection.num_parents + 1)
        else:
            best_organisms = self.get_n_best_organisms(self.size)

        # get the best and worst values in this subset of organisms
        best_value = best_organisms[-1].value
        worst_value = best_organisms[0].value

        # compute the fitnesses of the organisms in this subset
        for organism in best_organisms:
            organism.fitness = (organism.value - worst_value)/(best_value -
                                                               worst_value)

        # assign organisms not in this subset fitnesses of zero
        for organism in self.to_list():
            if organism not in best_organisms:
                organism.fitness = 0.0

    def get_best_in_pool(self):
        """
        Returns the organism in the pool with the best (smallest) objective
        function value.
        """

        # only need to look in the promotion set because that's where the best
        # ones live
        best_organism = self.promotion_set[0]
        for organism in self.promotion_set:
            if organism.value < best_organism.value:
                best_organism = organism
        return best_organism

    def get_worst_in_pool(self):
        """
        Returns the organism in the pool with the worst (largest) objective
        function value.
        """

        # only need to look in the queue because that's where the worst ones
        # live
        worst_organism = self.queue[0]
        for organism in self.queue:
            if organism.value > worst_organism.value:
                worst_organism = organism
        return worst_organism

    def compute_selection_probs(self):
        """
        Calculates and assigns selection probabilities to all the organisms in
        the pool.

        Precondition: the organisms in the pool all have up-to-date fitnesses
        """

        # get a list of the organisms in the pool, sorted by increasing fitness
        organisms = self.to_list()
        organisms.sort(key=lambda x: x.fitness, reverse=False)

        # get the denominator of the expression for selection probability
        denom = 0
        for organism in organisms:
            denom = denom + math.pow(organism.fitness, self.selection.power)

        # compute the selection probability and interval location for each
        # organism in best_organisms. The interval location is defined here as
        # the starting point of the interval
        selection_loc = 0
        for organism in organisms:
            organism.selection_prob = math.pow(organism.fitness,
                                               self.selection.power)/denom
            organism.selection_loc = selection_loc
            selection_loc = selection_loc + organism.selection_prob

    def get_n_best_organisms(self, n):
        """
        Returns a list containing the n best organisms in the pool, sorted in
        order of decreasing value.

        Precondition: all the organisms in the pool have up-to-date values

        Args:
            n: the number of best organisms to get
        """

        # get a list of all the organisms in the pool
        pool_list = self.to_list()

        # sort it in order of decreasing value
        pool_list.sort(key=lambda x: x.value, reverse=True)

        # get a sublist consisting of the last n elements of pool_list
        return pool_list[len(pool_list) - n:]

    def select_organisms(self, n, random):
        """
        Randomly selects n distinct organisms from the pool based on their
        selection probabilities.

        Returns a list containing n organisms.

        Args:
            n: how many organisms to select from the pool

            random: Python's built in PRNG

        Precondition: all the organisms in the pool have been assigned
            selection probabilities.
        """

        # list to hold the selected organisms
        selected_orgs = []
        pool_list = self.to_list()

        # keep going until we've selected enough
        while True:
            rand = random.random()
            # find the organism in the pool with the corresponding selection
            # probability
            for organism in pool_list:
                if rand >= organism.selection_loc and rand < (
                        organism.selection_loc + organism.selection_prob):
                    # check that we haven't already selected this one
                    if organism not in selected_orgs:
                        selected_orgs.append(organism)
                        # check if we've selected enough
                        if len(selected_orgs) == n:
                            return selected_orgs

    def print_summary(self, composition_space):
        """
        Prints out a summary of the organisms in the pool.

        Args:
            composition_space: the CompositionSpace object
        """

        # get a list of all the organisms in the pool
        pool_list = self.to_list()

        # sort it in order of decreasing fitness
        pool_list.sort(key=lambda x: x.value, reverse=False)

        # print out the value and fitness of each organism
        print('Summary of the pool:')
        for organism in pool_list:
            print('Organism {} has value {} and fitness {} and selection '
                  'probability {} '.format(organism.id, organism.value,
                                           organism.fitness,
                                           organism.selection_prob))

    def print_progress(self, composition_space):
        """
        Prints out either the best organism (for epa search) or the area/volume
        of the convex hull (for pd search).

        Args:
            composition_space: the CompositionSpace object
        """

        if composition_space.objective_function == 'epa':
            self.print_best_org()
        elif composition_space.objective_function == 'pd':
            self.print_convex_hull_area(composition_space)

    def print_best_org(self):
        """
        Prints out the id and value of the best organism in the pool
        """

        # get a list of the organisms in the pool
        pool_list = self.to_list()

        # sort it in order of decreasing fitness
        pool_list.sort(key=lambda x: x.fitness, reverse=True)

        # print out the best one
        print('Organism {} is the best and has value {} eV/atom '.format(
            pool_list[0].id, pool_list[0].value))

    def print_convex_hull_area(self, composition_space):
        """
        Prints out the area or volume of the convex hull defined by the
        organisms in the promotion set.
        """

        # make a phase diagram of just the organisms in the promotion set
        # (on the lower convex hull)
        pdentries = []
        for organism in self.promotion_set:
            pdentries.append(PDEntry(organism.composition,
                                     organism.total_energy))
        compound_pd = CompoundPhaseDiagram(pdentries,
                                           composition_space.endpoints)

        # get the data for the convex hull
        qhull_data = compound_pd.qhull_data
        # for some reason, the last point is positive, so remove it
        hull_data = np.delete(qhull_data, -1, 0)
        # make a ConvexHull object from the hull data
        convex_hull = ConvexHull(hull_data)
        # compute the volume or area
        if len(composition_space.endpoints) == 2:
            print('Area of the convex hull: {} '.format(convex_hull.area))
        else:
            print('Volume of the convex hull: {} '.format(convex_hull.volume))

    def to_list(self):
        """
        Returns a list containing all the organisms in the pool.
        """

        return self.promotion_set + list(self.queue)


class OffspringGenerator(object):
    """
    This class handles generating offspring organisms from the pool and the
    variations. Basically a place for the make_offspring_organism method to
    live.
    """

    def __init__(self):
        pass

    def make_offspring_organism(self, random, pool, variations, geometry,
                                id_generator, whole_pop, developer,
                                redundancy_guard, composition_space,
                                constraints):
        """
        Returns a developed, non-redundant, unrelaxed offspring organism
        generated with one of the variations.

        Args:
            TODO: seems like a ridiculous number of arguments...

        TODO: outline of how this method works
        """
        # holds the variations that have been tried
        tried_variations = []
        # the maximum number of times to try making a valid offspring with a
        # given variation before giving up and trying a different variation
        max_num_tries = 1000

        while True:
            variation = self.select_variation(random, tried_variations,
                                              variations)
            num_tries = 0
            while num_tries < max_num_tries:
                offspring = variation.do_variation(pool, random, geometry,
                                                   id_generator)
                offspring = developer.develop(offspring, composition_space,
                                              constraints, geometry, pool)
                # don't need to worry about replacement, since we're only
                # dealing with unrelaxed organisms here
                if (offspring is not None) and (
                        redundancy_guard.check_redundancy(
                            offspring, whole_pop) is None):
                    return offspring
                else:
                    num_tries = num_tries + 1

            tried_variations.append(variation)

            # if we've tried all the variations without success, then cycle
            # through them again
            if len(tried_variations) == len(variations):
                tried_variations = []

    def select_variation(self, random, tried_variations, variations):
        """
        Selects a variation that hasn't been tried yet based on their selection
        probabilities.

        Args:
            random:

            tried_variations: list of Variations that have already been
                unsuccessfully tried

            variations: list of all the Variations used in this search

        TODO: description
        """

        while True:
            rand = random.random()
            lower_bound = 0
            for i in range(len(variations)):
                if rand > lower_bound and rand <= (
                        lower_bound + variations[i].fraction) and \
                        variations[i] not in tried_variations:
                    return variations[i]
                else:
                    lower_bound = lower_bound + variations[i].fraction


class SelectionProbDist(object):
    """
    Defines the probability distribution over the fitnesses of the organisms
    in the pool that determines their selection probabilities.

    Comprised of two numbers: the number of organisms to select from, and the
    selection power.
    """

    def __init__(self, selection_params, pool_size):
        """
        Creates a selection probability distribution

        Args:
            selection_params: the parameters defining the distribution, as a
                dictionary

            pool_size: the size of the Pool (how many organisms it contains)
        """

        # default values
        self.default_num_parents = pool_size
        self.default_power = 1

        # if entire Selection block was left blank or set to default
        if selection_params in (None, 'default'):
            self.num_parents = self.default_num_parents
            self.power = self.default_power
        else:
            # check the num_parents parameter
            if 'num_parents' not in selection_params:
                self.num_parents = self.default_num_parents
            elif selection_params['num_parents'] in (None, 'default'):
                self.num_parents = self.default_num_parents
            # check that the given number isn't larger than the pool size
            elif selection_params['num_parents'] > pool_size:
                self.num_parents = self.default_num_parents
            else:
                self.num_parents = selection_params['num_parents']

            # check the selection_power parameter
            if 'power' not in selection_params:
                self.power = self.default_power
            elif selection_params['power'] in (None, 'default'):
                self.power = self.default_power
            else:
                self.power = selection_params['power']


class CompositionSpace(object):
    """
    Represents the composition space to be searched by the algorithm.
    """

    def __init__(self, endpoints):
        """
        Creates a CompositionSpace object, which is list of
        pymatgen.core.composition.Composition objects.

        Args:
            endpoints: the list of compositions, as strings
        """

        for i in range(len(endpoints)):
            endpoints[i] = Composition(endpoints[i])
            endpoints[i] = endpoints[i].reduced_composition

        self.endpoints = endpoints

        # for now, let's have the objective live here
        self.objective_function = self.infer_objective_function()

    def infer_objective_function(self):
        """
        Infers the objective function (energy per atom or phase diagram) based
        on the composition space.

        Returns either "epa" or "pd".
        """

        # if only one composition, then it must be an epa search
        if len(self.endpoints) == 1:
            return "epa"
        # otherwise, compare all the compositions and see if any of them are
        # different
        else:
            for point in self.endpoints:
                for next_point in self.endpoints:
                    if not point.almost_equals(next_point, 0.0, 0.0):
                        return "pd"
        # should only get here if there are multiple identical compositions in
        # end_points (which would be weird)
        return "epa"

    def get_all_elements(self):
        """
        Returns a list of all the elements
        (as pymatgen.core.periodic_table.Element objects) that are in the
        composition space.
        """

        # get each element type from the composition_space object
        elements = []
        for point in self.endpoints:
            elements = elements + point.elements

        # remove duplicates from the list of elements
        elements = list(set(elements))
        return elements

    def get_all_pairs(self):
        """
        Returns all possible pairs of elements in the composition space, as
        list of strings, where each string contains the symbols of two
        elements, separated by a space.

        Does not include self-pairs (e.g., "Cu Cu")
        """

        # get all the Element objects
        elements = self.get_all_elements()
        # if only one type of element, then no pairs, so return an empty list
        if len(elements) == 1:
            return []

        # list to hold the pairs of symbols
        pairs = []
        # get all the possible distinct pairs
        for i in range(0, len(elements) - 1):
            for j in range(i + 1, len(elements)):
                pairs.append(str(elements[i].symbol + " " +
                                 elements[j].symbol))
        return pairs

    def get_all_swappable_pairs(self):
        """
        Computes all pairs of elements in the the composition space that are
        allowed to be swapped based on their electronegativities. Only pairs
        whose electronegativities differ by 1.1 or less can be swapped.

        Returns a list of strings, where each string contains the symbols of
        two elements, separated by a space.

        Does not include self-pairs (e.g., "Cu Cu")
        """

        # start with the list of all possible pairs
        all_pairs = self.get_all_pairs()

        # get the subset of pairs that are allowed
        allowed_pairs = []
        for pair in all_pairs:
            element1 = Element(pair.split()[0])
            element2 = Element(pair.split()[1])
            # compute the electronegativity difference
            diff = abs(element1.X - element2.X)
            if diff <= 1.1:
                allowed_pairs.append(pair)
        return allowed_pairs


class StoppingCriteria(object):
    """
    Defines when the search should be stopped.
    """

    def __init__(self, stopping_parameters, composition_space):
        """
        Args:
            stopping_parameters: a dictionary of parameters

            composition_space: a CompositionSpace object
        """

        # set the defaults
        if len(composition_space.endpoints) == 1:
            # for fixed composition searches
            self.default_num_energy_calcs = 800
        elif len(composition_space.endpoints) == 2:
            # for binary phase diagram searches
            self.default_num_energy_calcs = 1000
        elif len(composition_space.endpoints) == 3:
            # for ternary phase diagram searches
            self.default_num_energy_calcs = 3000
        elif len(composition_space.endpoints) == 4:
            # for quaternary phase diagram searches
            self.default_num_energy_calcs = 6000

        self.default_value_achieved = None
        self.default_found_structure = None

        # whether or not the stopping criteria are satisfied
        self.are_satisfied = False
        # to keep track of how many energy calculations have been done
        self.calc_counter = 0

        # set defaults if stopping_parameters equals 'default' or None
        if stopping_parameters in (None, 'default'):
            self.num_energy_calcs = self.default_num_energy_calcs
            self.value_achieved = self.default_value_achieved
            self.found_structure = self.default_found_structure
        # check each flag to see if it's been included, and if so, whether it
        # has been set to default or left blank
        else:
            # value achieved
            if 'value_achieved' in stopping_parameters:
                if stopping_parameters['value_achieved'] in (None, 'default'):
                    self.value_achieved = self.default_value_achieved
                # checking objective function values only makes sense for
                # fixed-composition searches
                elif composition_space.objective_function == 'epa':
                    self.value_achieved = stopping_parameters['value_achieved']
            else:
                self.value_achieved = self.default_value_achieved

            # found structure
            if 'found_structure' in stopping_parameters:
                if stopping_parameters['found_structure'] in (None, 'default'):
                    self.found_structure = self.default_found_structure
                else:
                    # read the structure from the file
                    self.path_to_structure_file = stopping_parameters[
                        'found_structure']
                    self.found_structure = Structure.from_file(
                            stopping_parameters['found_structure'])
            else:
                self.found_structure = self.default_found_structure

            # num energy calcs
            if 'num_energy_calcs' in stopping_parameters:
                if stopping_parameters['num_energy_calcs'] in (None,
                                                               'default'):
                    # only use default if other two methods haven't been given
                    if self.value_achieved is None and \
                            self.found_structure is None:
                        self.num_energy_calcs = self.default_num_energy_calcs
                    else:
                        self.num_energy_calcs = None
                else:
                    self.num_energy_calcs = stopping_parameters[
                        'num_energy_calcs']
            # only use default if other two methods haven't been specified
            elif self.value_achieved is None and self.found_structure is None:
                self.num_energy_calcs = self.default_num_energy_calcs
            else:
                self.num_energy_calcs = None

    def update_calc_counter(self):
        """
        If num_energy_calcs stopping criteria is being used, increments calc
        counter and updates are_satisfied if necessary.
        """

        if self.num_energy_calcs is not None:
            self.calc_counter = self.calc_counter + 1
            if self.calc_counter >= self.num_energy_calcs:
                self.are_satisfied = True

    def check_organism(self, organism):
        """
        If value_achieved or found_structure stopping criteria are used, checks
        if the relaxed organism satisfies them, and updates are_satisfied.

        Args:
            organism: a relaxed organism whose value has been computed
        """

        if self.value_achieved is not None:
            if organism.value <= self.value_achieved:
                self.are_satisfied = True
        if self.found_structure is not None:
            if self.found_structure.matches(organism.structure):
                self.are_satisfied = True
