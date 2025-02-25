# grid_tesselate.py
# create triangles from a top and bottom numpy 2D array, including walls

'''
@author:     Chris Harding
@license:    GPL
@contact:    charding@iastate.edu

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.
  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''
# CH: Apr. 2019: converted to Python 3
# CH: Feb. 2018: added use of tempfile as file buffer to lower memory footprint
# CH: Feb. 2017: added calculations for normals in stl files
# CH: Jan. 22, 16: putting the vert index behind a comment makes some programs crash
#                  when loading the obj file, so I removed those.
# FIX: (CH, Nov.16,15): make the vertex index a per grid attribute rather than
#  a vertex class attribute as this seem to index not found fail eventually when
#  multiple grids are processed together.
# CH July 2015

import numpy as np
import copy   # to copy objects
import warnings # for muting warnings about nan in e.g. nanmean()
import struct # for making binary STL
#from collections import OrderedDict # for vertex indices dict
import sys
import multiprocessing
import tempfile
import io

# get root logger, will later be redirected into a logfile
import logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)



from touchterrain.common.vectors import Vector, Point  # local copy of vectors package

# function to calculate the normal for a triangle
def get_normal(tri):
    "in: 3 verts, out normal (nx, ny,nz) with length 1"
    
    (v0, v1, v2) = tri
    p0 = Point.from_list(v0.get())
    p1 = Point.from_list(v1.get())
    p2 = Point.from_list(v2.get())
    a = Vector.from_points(p1, p0)
    b = Vector.from_points(p1, p2)
    #print p0,p1, p2
    #print a,b
    c = a.cross(b)
    #print c
    m = float(c.magnitude())
    if m == 0:
        normal = [0, 0, 0]
    else:
        normal = [c.x/m, c.y/m, c.z/m]
    return normal


class vertex(object):
    def __init__(self, x,y,z, vertex_idx_from_grid):
        self.coords = [x,y,z] # needs to be a list for zigzag magic to be able to change coords
        self.vert_idx = vertex_idx_from_grid # list of indices kept by grid (class attribute self.vi)

        # for non obj file this is set to -1, and there's no need to deal with vertex indices
        if self.vert_idx != -1:
            # This creates a  dict (a grid class attribute) with a tuple of the
            # 3 coords as key and a int as value. The int is a running index i.e. for each new
            # (not yet hashed) vertex this index just increases by 1, based the number of dict
            # entries. If a vertex has coords that already exist in the dict, nothing needs to be done.
            # This ensures that each index number is unique but can be shared by multiple indices
            # (e.g. when 2 triangles have vertices at exactly the same coords)


            # if key does not yet exist ...
            if tuple(self.coords) not in self.vert_idx: # can't hash lists
                # hash with 3D coords with a running number as Id
                self.vert_idx[tuple(self.coords)] = len(self.vert_idx) # ... store it with current length (= number of elements) as index value
            else: # this vertex already has an Id
                #print self.coords, len(self.vert_idx) # DEBUG
                pass

    def get_id(self):
        '''return Id for my coords'''
        i = self.vert_idx[tuple(self.coords)]
        return i

    def get(self):
        "returns [x,y,z] list of vertex"
        return self.coords

    def get_copy(self):
        "returns copy of vertex"
        x,y,z = self.coords[:] # get copy and unroll
        # vert_idx must NOT be a copy b/c this dict is a pointer to the grid object
        # i.e. it's "global" (class attribute) and shared by all vertices 
        vert_idx = self.vert_idx         
        cp = vertex(x,y,z, vert_idx) # make new vertex from copied coords
        return cp

    def __str__(self):
        return "%.2f %.2f %.2f " % (self.coords[0], self.coords[1], self.coords[2])

    def __getitem__(self, index): 
        "enables use of index brackets for vertex objects: v[0] returns coords[0]"
        return self.coords[index]




class quad(object):
    """return list of 2 triangles (counterclockwise) per quad
       wall quads will NOT subdivide their quad into subquads if they are too skinny
       as this would require to re-index the entire mesh. However, I left the subdive
       stuff in in case we want to re-visit it later.
    """
    # class attribute, use quad.too_skinny_ratio
    too_skinny_ratio = 0.1 # border quads with a horizontal vs vertical ratio smaller than this will be subdivided

    # order is NE, NW, SW, SE
    # can be just a triangle, if it just any 3 ccw consecutive corners 
    def __init__(self, v0, v1, v2, v3=None): 
        self.vl = [v0, v1, v2, v3]
        self.subdivide_by = None # if not None, we need to subdivide the quad into that many subquads

    def get_copy(self):
        ''' returns a copy of the quad'''
        vl = self.vl[:]
        cp = quad(vl[0].get_copy(), vl[1].get_copy(), vl[2].get_copy(), vl[3].get_copy())
        return cp

    def check_if_too_skinny(self, direction):
        '''if a border quad is too skinny it will to be subdivided into multiple quads'''
        #print direction, [str(v) for v in self.vl]

        # order of verts will be different for N,S vs E,W walls!
        if direction in ("S", "N"): # '-49.50 49.50 0.00 ', '-49.50 49.50 10.00 ', '-50.00 49.50 10.00 ', '-50.00 49.50 0.00 '
            horz_dist = abs(self.vl[0][0] - self.vl[2][0]) # x diff of v0 and v2
            max_elev = max(self.vl[1][2], self.vl[2][2]) # max elevation of v1 vs v2
            min_elev = min(self.vl[0][2], self.vl[3][2]) # min elevation v0 vs v3
            vert_dist = max_elev - min_elev # z diff of v0 and v1
        else: # -49.50 50.00 10.00 ', '-49.50 49.50 10.00 ', '-49.50 49.50 0.00 ', '-49.50 50.00 0.00 '
            horz_dist = abs(self.vl[0][1] - self.vl[1][1]) # y diff of v0 and v1
            max_elev = max(self.vl[0][2], self.vl[1][2]) # max elevation of v0 vs v1
            min_elev = min(self.vl[2][2], self.vl[3][2]) # min elevation v2 vs v3
            vert_dist = max_elev - min_elev # z diff of v0 and v1
        if vert_dist == 0: return # walls can be 0 height

        ratio = horz_dist / float (vert_dist)
        #print ratio, quad.too_skinny_ratio, quad.too_skinny_ratio / ratio
        if ratio < quad.too_skinny_ratio:
            sb = int(quad.too_skinny_ratio / ratio)
            self.subdivide_by = sb

    def get_triangles(self):
        "return list of 2 triangles (counterclockwise)"
        v0,v1,v2,v3 = self.vl[0],self.vl[1],self.vl[2],self.vl[3]
        t0 = (v0, v1, v2)  # verts of first triangle

        # if v3 is None, we only return t0
        if v3 != None:
            t1 = (v0, v2, v3)  # verts of second triangle
            return (t0,t1)
        else:
            return(t0, None)

    def get_triangles_with_indexed_verts(self):
        "return list of 2 triangles (counterclockwise) as vertex indices"

        vertidx = [] # list of the 4 verts as index
        for v in self.vl: # quad as list of 4 verts, each as (x,y,z)
            if v != None: # v3 could be None
                vi = v.get_id()
                vertidx.append(vi)
            #print v,vi

        t0 = (vertidx[0], vertidx[1], vertidx[2])  # verts of first triangle
        # if v3 is None(i.e. we didn't get a 4. index), we only return t0
        if len(vertidx) > 3:
            t1 = (vertidx[0], vertidx[2], vertidx[3])  # verts of second triangle
            return (t0,t1)
        else:
            return(t0, None)

    '''
    # splits skinny triangles
    def get_triangles(self, direction=None):
        """return list of 2 triangles (counterclockwise) per quad
           wall quads will subdivide their quad into subquads if they are too skinny
        """
        v0,v1,v2,v3 = self.vl[0],self.vl[1],self.vl[2],self.vl[3]

        # do we need to subdivide?
        if self.subdivide_by == None: # no, either not a wall or a chunky wall
            t0 = (v0, v1, v2)  # verts of first triangle
            t1 = (v0, v2, v3)  # verts of second triangle
            return (t0,t1)

        else:
            # subdivde into sub quads and return their triangles

            # order of verts will be different for N,S vs E,W walls!
            if direction in ("S", "N"): # '-49.50 49.50 0.00 ', '-49.50 49.50 10.00 ', '-50.00 49.50 10.00 ', '-50.00 49.50 0.00 '
                horz_dist = abs(self.vl[0][0] - self.vl[2][0]) # x diff of v0 and v2
                max_elev = max(self.vl[1][2], self.vl[2][2]) # max elevation of v1 vs v2
                min_elev = min(self.vl[0][2], self.vl[3][2]) # min elevation v0 vs v3
                vert_dist = max_elev - min_elev # z diff of v0 and v1
            else: # -49.50 50.00 10.00 ', '-49.50 49.50 10.00 ', '-49.50 49.50 0.00 ', '-49.50 50.00 0.00 '
                horz_dist = abs(self.vl[0][1] - self.vl[1][1]) # y diff of v0 and v1
                max_elev = max(self.vl[0][2], self.vl[1][2]) # max elevation of v0 vs v1
                min_elev = min(self.vl[2][2], self.vl[3][2]) # min elevation v2 vs v3
                vert_dist = max_elev - min_elev # z diff of v0 and v1



            tri_list = []

            # for finding the height of the sub quads I don't care about the different vert order
            z_list =[v[2] for v in self.vl]
            z_top = max(z_list) # z height of the top (take min() b/c one might be higher)
            z_bot = min(z_list) # z height at bottom
            z_dist = z_top - z_bot # distance to be

            #self.subdivide_by = 3 # DEBUG

            qheight = z_dist / float(self.subdivide_by) # height (elevation dist) of each quad
            height_list = [ z_top - qheight * i for i in range(self.subdivide_by+1) ] # list of h

            # make new subquads and return a list of their triangles
            vl_copy = copy.deepcopy(self.vl) # must make a deep copy, otherwise changing the subquads affect the current quad
            tl = [] # triangle list

            bottom_height_list = height_list[1:]
            for n,_ in enumerate(bottom_height_list):
                v0_,v1_,v2_,v3_ = vl_copy[0], vl_copy[1], vl_copy[2],vl_copy[3] # unroll copy
                #print n,v0_,v1_,v2_,v3_

                # as order of verts will be different for N,S vs E,W walls we need 2 different cases
                if direction in ("N", "S"):
                    top_inds = (1,2)
                    bot_inds = (0,3)
                else:
                    top_inds = (0,1)
                    bot_inds = (2,3)


                # top verts
                if n > 0: # don't change top z for topmost sub quad
                    h = height_list[n]
                    v= vl_copy[top_inds[0]] # first vertex of subquad
                    v.coords[2] = h         # set its z value
                    v= vl_copy[top_inds[1]]
                    v.coords[2] = h

                # bottom verts
                if n < len(bottom_height_list): # don't change bottom z for bottommost sub quad
                    h = height_list[n+1]
                    v = vl_copy[bot_inds[0]]
                    v.coords[2] = h
                    v = vl_copy[bot_inds[1]]
                    v.coords[2] = h

                # make a sub quad
                sq = copy.deepcopy(quad(vl_copy[0], vl_copy[1], vl_copy[2],vl_copy[3])) # each subquad needs to be its own copy
                #print n, sq,

                t0,t1 = sq.get_triangles()
                tl.append(t0)
                tl.append(t1)

            return tl
    '''

    def __str__(self):
        rs ="  "
        for n,v in enumerate(self.vl):
            rs = rs + "v" + str(n) + ": " + str(v) + "  "
        return rs


class cell(object):
    '''a cell with a top and bottom quad, constructor: uses refs and does NOT copy ...
       except for triangle cells
       '''
    def __init__(self, topquad, bottomquad, borders, is_tri_cell=False):
        self.topquad = topquad
        self.bottomquad = bottomquad
        self.borders = borders
        self.is_tri_cell = is_tri_cell

    def __str__(self):
        r = hex(id(self)) + "\n top:" + str(self.topquad) + "\n btm:" + str(self.bottomquad) + "\n borders:\n"
        for d in ["N", "S", "E", "W"]:
            if self.borders[d] != False:
                r = r + "  " + d + ": " + str(self.borders[d]) + "\n"
        return r

    def check_for_tri_cell(self):
        """Returns True if cell has borders on 2 consecutive sides False otherwise.
           Returns False is cell is already a tri-cell""" 
        if self.is_tri_cell == True: return None
        b = self.borders

        # Count borders (non-False will be a pointer to a wall quad, i.e. True is not used here!
        num_borders = 0
        for d in ["N", "S", "E", "W"]:
            if b[d] != False: num_borders += 1

        if num_borders == 2:
            if b["N"] != False and b["S"] != False: return False
            if b["E"] != False and b["W"] != False: return False
        else: 
            return False # cannot be triangelized

        #print("tricell:", num_borders, b)
        return True # 2 touching sides
    
    def convert_to_tri_cell(self):
        """Collapses the top and bottom quad into a triangle based on its 2 border walls,
        replaces one of the 2 border walls with a diagonal wall and the other with False.
        returns None, sets is_tri_cell to True"""
        if self.is_tri_cell == True: return None

        b = self.borders    
        tq =  self.topquad.get_copy()
        bq =  self.bottomquad.get_copy()     # NW SE SW NE
        tvl = tq.vl #                           0  1  2  3
        bvl = bq.vl # vertex order in quad is   0  3  2  1
        
        # Collapse the quad into a triangle depending on where the 2 borders are
        # In addition we need to get rid of one wall and overwrite the other
        # with a new diagonal wall 
        
        if b["N"] != False and b["W"] != False:
            self.topquad = quad(tvl[3], tvl[1], tvl[2], None) # ccw, order doesn't matter
            self.bottomquad = quad(bvl[1], bvl[2], bvl[3], None) # cw!
            b["N"] = quad(tvl[1], tvl[3], bvl[1], bvl[3]) # diagonal wall (ccw!)
            b["W"] = False # no used anymore
        elif b["N"] != False and b["E"] != False: 
            self.topquad = quad(tvl[0], tvl[1], tvl[2], None)
            self.bottomquad = quad(bvl[0], bvl[2], bvl[3], None) 
            b["N"] = quad(tvl[0], tvl[2], bvl[2], bvl[0])
            b["E"] = False 
        elif b["S"] != False and b["E"] != False: 
            self.topquad = quad(tvl[3], tvl[0], tvl[1], None)
            self.bottomquad = quad(bvl[3], bvl[0], bvl[1], None)
            b["S"] = quad(tvl[3], tvl[1], bvl[3], bvl[1])
            b["E"] = False
        elif b["S"]!= False and b["W"] != False: 
            self.topquad = quad(tvl[2], tvl[3], tvl[0], None)
            self.bottomquad = quad(bvl[0], bvl[1], bvl[2], None)
            b["S"] = quad(tvl[2], tvl[0], bvl[0], bvl[2])
            b["W"] = False
        else:
            print("convert_to_tri_cell() got invalid border config:", (self.borders), " - aborting")
            sys.exit() 
            
        self.is_tri_cell = True

        return None



#profiling decorator
# https://medium.com/fintechexplained/advanced-python-learn-how-to-profile-python-code-1068055460f9
import cProfile
import functools
import pstats
import tempfile
def profile_me(func):
    @functools.wraps(func)
    def wraps(*args, **kwargs):
        print("profiling started")
        file = tempfile.mktemp()
        profiler = cProfile.Profile()
        profiler.runcall(func, *args, **kwargs)
        profiler.dump_stats(file)
        metrics = pstats.Stats(file)
        metrics.strip_dirs().sort_stats('time').print_stats(100)
    return wraps





class grid(object):
    " makes cells from two numpy arrays (top, bottom) of the same shape. tile_info is a simple dict"
    #@profile # https://pypi.org/project/memory-profiler/
    def __init__(self, top, bottom, tile_info):
        '''top: top elevation raster, must hang over by 1 row/column on each side (be already padded)
        bottom: None => bottom elevation is 0, otherwise a 8 bit raster that will be resized to top's size
        tile_info: dict with info about the current tile + some tile global settings'''

        if tile_info["fileformat"] == 'obj':
            self.vi = {} #OrderedDict() # set class attrib to empty dict, will be filled with vertex indices
        else:
            self.vi = -1 # means: don't use vertex indices
        self.cells = None # stores the cells, for now a 2D array of cells, but could also be a list of cells (later)

        #print top.shape, bottom.shape
        tp = top.dtype # dtype('float32')
        if not str(tp).startswith("float"): print("Warning: expecting float raster!")

        # Important: in 2D numpy arrays, x and y are "flipped" in the sense that when printing top
        # top[0,0] appears to the upper left (NW) corner and [0,1] (East) of it:
        #[[11  12 13]       top[0,1] => 12
        # [Nan 22 23]       top[2,0] => NaN (Not a value -> undefined elevation)
        # [31  32 33]       top[2,1] => 32
        # [41  42 43]]
        # i.e. height (y) is encoded in the first (0) dimension, width in the second (1),
        # the 'edge" padded version for a top left tile might be this, the right and bottom fringe
        # are part of the adjacent tiles, which must be used in the interpolation to result in no-seam edges
        #      [[11, 11, 12, 13, 14],
               #[11, 11, 12, 13, 14],
               #[Nan,Nan,22, 23, 24],
               #[31, 31, 32, 33, 34],
               #[41, 41, 42, 43, 44],
               #[41, 51, 52, 54, 55]])


        # DEBUG: normalized (0 - 1) xy coord increment per cell
        #y_norm_delta  = 1 / float(top.shape[0]) # y (north-south) direction
        #x_norm_delta  = 1 / float(top.shape[1]) # x (east-west) direction
        #print "normalized x/y delta:", x_norm_delta, y_norm_delta

        # cell size (x and y delta)
        cell_size = tile_info["pixel_mm"]

        have_nan = np.isnan(np.sum(top)) # True => we have NaN values, see http://stackoverflow.com/questions/6736590/fast-check-for-nan-in-numpy
        tile_info["have_nan"] = have_nan # save for later
        #print "have_nan", have_nan

        # Jan 2019: no idea why but sometimes changing top also changes the elevation
        # array of another tile in the tile list
        # for now I make a copy of the incoming top (calling it ro_top)
        ro_top = top
        top = ro_top.copy() # writeable

        # if bottom is not an ndarray, we don't have a bottom raster, so the bottom is a constant
        if isinstance(bottom, np.ndarray) == False:  
            bottom = 0
            have_bottom_array = False
        elif isinstance(bottom, np.ndarray) == True and have_nan == True:  # Can't use bottom array with NaN
            have_bottom_array = False
            bottom = 0
            print("Top has NaN values, requested bottom array will be ignored!")
        else: 
            have_bottom_array = True # got bottom array and no NaNs on top

        tile_info["have_bottom_array"] = have_bottom_array

        # real world (pre-scale) min_elev, either user-given or min of top
        if tile_info["min_elev"] == None: # NOT set by user
            tile_info["min_elev"] = np.nanmin(top)
        # else use user-given min_elev 


        # Coordinates are in mm (for 3D printing on a buildplate)
        if tile_info["use_geo_coords"] == None:

            #
            # convert top's elevation from real word elevation (m) to model height (mm)
            #

            #print top.astype(int)
            top -= float(tile_info["min_elev"]) # subtract tile-wide min from top
            #print np.nanmin(top), np.nanmax(top)
            #print top.astype(int)
            scz = 1 / float(tile_info["scale"]) * 1000.0 # scale z to mm
            #print tile_info["scale"], tile_info["z_scale"], scz
            top *= scz * tile_info["z_scale"] # apply z-scale
            #print top.astype(int)
            
            top += tile_info["base_thickness_mm"] # add base thickness
            print("top min/max:", np.nanmin(top), np.nanmax(top))
            #print top.astype(int)

        else:  # using geo coords - thickness is meters)
            bottom = tile_info["min_elev"] - tile_info["base_thickness_mm"] * 10
            logger.info("Using geo coords with a base thickness of " + str(tile_info["base_thickness_mm"] * 10) + " meters")

        # post-scale (i.e. in mm) max elev
        tile_info["max_elev"] = np.nanmax(top)



        # max index in x and y for "inner" raster
        xmaxidx = top.shape[1]-2
        ymaxidx = top.shape[0]-2
        #print range(1, xmaxidx+1), range(1, ymaxidx+1)

        # offset so that 0/0 is the center of this tile (local) or so that 0/0 is the lower left corner of all tiles (global)
        if tile_info["tile_centered"] == False: # global offset, best for looking at all tiles together
            offsetx = -tile_info["tile_width"]  * (tile_info["tile_no_x"]-1)  # tile_no starts with 1! This is the top end of the tile, not 0!
            offsety = -tile_info["tile_height"] * (tile_info["tile_no_y"]-1)  + tile_info["tile_height"] * tile_info["ntilesy"]

        else: # local centered for printing
            offsetx = tile_info["tile_width"] / 2.0
            offsety = tile_info["tile_height"] / 2.0
            # put extent of current into tile info dict (may later be needed for 2 bottom triangles)
            
        #print offsetx, offsety


        # geo coords are in meters (UTM). Place the tiles so that the center is at 0/0, which is what Blender GIS needs.
        # tile_centered is ignored for geo coords
        if tile_info["use_geo_coords"] != None:

            geo_transform = tile_info["geo_transform"]
            cell_size = abs(geo_transform[1]) # rw pixel size of geotiff in m
            tile_width_m  = xmaxidx * cell_size # number of (unpadded) pixels of current tile
            tile_height_m = ymaxidx * cell_size

            # size in meters but 0/0 of full model is at its center (BlenderGIS)
            if tile_info["use_geo_coords"] == "centered":

                offsetx = -tile_width_m  * (tile_info["tile_no_x"]-1)
                offsety = tile_height_m  * tile_info["ntilesy"] - tile_height_m * (tile_info["tile_no_y"]-1)

                # center by half the total size
                offsetx += (tile_info["full_raster_width"] * cell_size) / 2
                offsety -= (tile_info["full_raster_height"] * cell_size) / 2

                # correct for off-by-1 cells
                offsetx -= cell_size
                offsety += cell_size

            # size in meters but the UTM zone's origin is used, i.e. each vertex is in full
            # UTM coordinates. Not sure what CAD/modelling system uses that but if needed it's an option.
            else:  # "UTM"

                offsetx = -tile_width_m  * (tile_info["tile_no_x"]-1)
                offsety = -tile_height_m * (tile_info["tile_no_y"]-1)

                offsetx = -geo_transform[0] + offsetx # UTM x of upper left corner
                offsety =  geo_transform[3] + offsety # UTM y

        
        # put extent of current into tile info dict (may later be needed for 2 bottom triangles)
        if tile_info["tile_centered"] == False:
            tile_info["W"] = tile_info["tile_width"]  * (tile_info["tile_no_x"]-1)  
            tile_info["E"] = tile_info["W"] + tile_info["tile_width"]
            tot_height = tile_info["tile_height"] * tile_info["ntilesy"]
            # y tiles index goes top(0) DOWN to bottom
            tile_info["N"] = tot_height - (tile_info["tile_height"] * (tile_info["tile_no_y"]-1))
            tile_info["S"] = tile_info["N"] - tile_info["tile_height"]
        else:
            tile_info["W"] = -tile_info["tile_width"] / 2
            tile_info["E"] =  tile_info["tile_width"] / 2
            tile_info["S"] = -tile_info["tile_height"] / 2
            tile_info["N"] =  tile_info["tile_height"] / 2

        # store cells in an array, init to None
        self.cells = np.empty([ymaxidx, xmaxidx], dtype=cell)

        # report progress in %
        percent = 10
        pc_step = int(ymaxidx/percent) + 1
        progress = 0
        print("creating internal triangle data structure for", multiprocessing.current_process(), file=sys.stderr)

        for j in range(1, ymaxidx+1):    # y dimension for looping within the +1 padded raster
            if j % pc_step == 0:
                progress += percent
                print(progress, "%", multiprocessing.current_process(), file=sys.stderr)

            for i in range(1, xmaxidx+1):# x dim.
                #print "y=",j," x=",i, " elev=",top[j,i]

                # if center elevation of current cell is NaN, set it to None and skip the rest
                if have_nan and np.isnan(top[j,i]):
                    self.cells[j-1,i-1] = None
                    continue


                # x/y coords of cell "walls", origin is upper left
                E = (i-1) * cell_size - offsetx # index -1 as it's ref'ing to top, not ptop
                W = E + cell_size  # Ch NOv 2021: I think E and W are flipped (?) but I must correct for that later somewhere (?)
                N = -(j-1) * cell_size + offsety # y is flipped to negative
                S = N - cell_size
                #print(i,j, " ", E,W, " ",  N,S, " ", top[j,i])


                ## Which directions will need to have a wall?
                # True means: we have an adjacent cell and need a wall in that direction
                borders =   dict([[drct,False] for drct in ["N", "S", "E", "W"]]) # init                    
                
                # set walls for fringe cells
                if j == 1        : borders["N"] = True
                if j == ymaxidx  : borders["S"] = True
                if i == 1        : borders["W"] = True
                if i == xmaxidx  : borders["E"] = True

                if not have_nan:
                    # interpolate elevation of four corners (array order is top[y,x]!!!!)
                    NEelev = (top[j+0,i+0] + top[j-1,i-0] + top[j-1,i+1] + top[j-0,i+1]) / 4.0
                    NWelev = (top[j+0,i+0] + top[j+0,i-1] + top[j-1,i-1] + top[j-1,i+0]) / 4.0
                    SEelev = (top[j+0,i+0] + top[j-0,i+1] + top[j+1,i+1] + top[j+1,i+0]) / 4.0
                    SWelev = (top[j+0,i+0] + top[j+1,i+0] + top[j+1,i-1] + top[j+0,i-1]) / 4.0
                else:
                    #print "NSWE", top[j-1,i], top[j+1,i], top[j,i-1], top[j,i+1]
                    with warnings.catch_warnings():
                        warnings.filterwarnings('error')
                        try:
                            if np.isnan(top[j-1,i]): borders["N"] = True
                            if np.isnan(top[j+1,i]): borders["S"] = True
                            if np.isnan(top[j,i-1]): borders["W"] = True
                            if np.isnan(top[j,i+1]): borders["E"] = True
                        except RuntimeWarning:
                            pass # nothing wrong - just here to ignore the warning


                    # interpolate each corner with possible NaNs, using mean()
                    # Note: if we have 1 or more NaNs, we get a warning: warnings.warn("Mean of empty slice", RuntimeWarning)
                    # but if the result of ANY corner is NaN (b/c it used 4 NaNs), skip this cell entirely by setting it to None instead a cell object
                    with warnings.catch_warnings():
                        warnings.filterwarnings('error')
                        NEar = np.array([top[j+0,i+0], top[j-1,i-0], top[j-1,i+1], top[j-0,i+1]])
                        NWar = np.array([top[j+0,i+0], top[j+0,i-1], top[j-1,i-1], top[j-1,i+0]])
                        SEar = np.array([top[j+0,i+0], top[j-0,i+1], top[j+1,i+1], top[j+1,i+0]])
                        SWar = np.array([top[j+0,i+0], top[j+1,i+0], top[j+1,i-1], top[j+0,i-1]])

                        try: # nanmean is expensive, so only use it when actually needed
                            NEelev = np.nanmean(NEar) if np.isnan(np.sum(NEar)) else (top[j+0,i+0] + top[j-1,i-0] + top[j-1,i+1] + top[j-0,i+1]) / 4.0  
                            NWelev = np.nanmean(NWar) if np.isnan(np.sum(NWar)) else (top[j+0,i+0] + top[j+0,i-1] + top[j-1,i-1] + top[j-1,i+0]) / 4.0
                            SEelev = np.nanmean(SEar) if np.isnan(np.sum(SEar)) else (top[j+0,i+0] + top[j-0,i+1] + top[j+1,i+1] + top[j+1,i+0]) / 4.0
                            SWelev = np.nanmean(SWar) if np.isnan(np.sum(SWar)) else (top[j+0,i+0] + top[j+1,i+0] + top[j+1,i-1] + top[j+0,i-1]) / 4.0

                        except RuntimeWarning: #  corner is surrounded by NaN eleveations - skip this cell
                            #print(j-1, i-1, ": elevation of at least one corner of this cell is NaN - skipping cell")
                            #print " NW",NWelev," NE", NEelev, " SE", SEelev, " SW", SWelev # DEBUG
                            num_nans = sum(np.isnan(np.array([NEelev, NWelev, SEelev, SWelev]))) # is ANY of the corners NaN?
                            if num_nans > 0: # yes, set cell to None and skip it ...
                                self.cells[j-1,i-1] = None
                                continue

                #NWelev = NEelev = SEelev = SWelev = ptop[j,i] # DEBUG, set all corners to center elev
                #print " NW",NWelev," NE", NEelev, " SE", SEelev, " SW", SWelev # DEBUG

                #
                # Make top and bottom quads and wall. Note that here we flip x and y coordinate axis to the system used in 3D graphics
                #

                # make top quad (x,y,z)    vi is the vertex index dict of the grids
                NEt = vertex(E, N, NWelev, self.vi)  # yes, NEt gets the z of NWelev, has to do with coordinate system change
                NWt = vertex(W, N, NEelev, self.vi)
                SEt = vertex(E, S, SWelev, self.vi)
                SWt = vertex(W, S, SEelev, self.vi)
                topq = quad(NEt, SEt, SWt, NWt) # with this vertex order, a certain vertex order is needed to make the 2 triangles be counter clockwise and so point outwards
                #print topq

                #
                # make bottom quad  
                #

                # for bottom array (which implies non-Nan) or NaN (implies no bottom array) get corner values
                if have_bottom_array == True:
                    NEelev = (bottom[j+0,i+0] + bottom[j-1,i-0] + bottom[j-1,i+1] + bottom[j-0,i+1]) / 4.0
                    NWelev = (bottom[j+0,i+0] + bottom[j+0,i-1] + bottom[j-1,i-1] + bottom[j-1,i+0]) / 4.0
                    SEelev = (bottom[j+0,i+0] + bottom[j-0,i+1] + bottom[j+1,i+1] + bottom[j+1,i+0]) / 4.0
                    SWelev = (bottom[j+0,i+0] + bottom[j+1,i+0] + bottom[j+1,i-1] + bottom[j+0,i-1]) / 4.0
                else:
                    NEelev = NWelev = SEelev = SWelev = bottom # otherwise use uniform bottom elevation 

                # from whatever bottom values we have now, make the bottom quad
                # (if we do the 2 tri bottom, these will end up not be used for the bottom but they may be used for any walls ...)
                NEb = vertex(E, N, NWelev, self.vi)
                NWb = vertex(W, N, NEelev, self.vi)
                SEb = vertex(E, S, SWelev, self.vi)
                SWb = vertex(W, S, SEelev, self.vi)
                botq = quad(NEb, NWb, SWb, SEb)
                #print botq


                # Quads for walls: in borders dict, replace any True with a quad of that wall
                if borders["N"] == True: borders["N"] = quad(NEb, NEt, NWt, NWb)
                if borders["S"] == True: borders["S"] = quad(SWb, SWt, SEt, SEb)
                if borders["E"] == True: borders["E"] = quad(NWt, SWt, SWb, NWb)
                if borders["W"] == True: borders["W"] = quad(SEt, NEt, NEb, SEb)

                # Make cell
                if tile_info["no_bottom"] == True:
                    c = cell(topq, None, borders) # omit bottom - do not fill with 2 tris later (may have NaNs)
                else:
                    if have_nan == True or have_bottom_array == True or tile_info["fileformat"] == "obj": 
                    # Note: obj doesn't currently support simple bottoms
                        c = cell(topq, botq, borders) # full cell: top quad, bottom quad and wall quads
                    else:
                        c = cell(topq, None, borders) # omit bottom, will fill with 2 tris later

                # DEBUG: store i,j, and central elev
                #c.iy = j-1
                #c.ix = i-1
                #c.central_elev = top[j-1,i-1]

                # if we have nan cells, do some postprocessing on this cell to get rid of stair case patterns
                # This will create special triangle cells that have a triangle of any orientation at top/bottom, which 
                # are flagged as is_tri_cell = True, and have only v0, v1 and v2. One border is deleted, the other
                # is set as a diagonal wall.
                if have_nan == True and tile_info["smooth_borders"] == True:
                    #print(i,j, c.borders)
                    if c.check_for_tri_cell():
                        c.convert_to_tri_cell()  # collapses top and bot quads into a triangle quad and make diagonal wall
                

                # put cell into array of cells (self.cells is NOT padded, => -1)
                self.cells[j-1,i-1] = c
                #print self.cells[j-1,i-1]
                #print j-1,i-1, self.cells

        #print self.cells
        #print vertex.vert_idx

        print("100%", multiprocessing.current_process(), "\n", file=sys.stderr)




    def create_zigzag_borders(self, num_cells_per_zig = 100, zig_dist_mm = 0.15, zig_undershoot_mm = 0.05):
        """ post process the border quads so that it follows a zig-zag pattern """

        assert num_cells_per_zig > 1, "create_zigzag_borders() error: num_cells_per_zig =" + str(num_cells_per_zig)

        # number of cells in x and y     grid is cells[y,x]
        ncells_x = self.cells.shape[1]
        ncells_y = self.cells.shape[0]

        ncpz = num_cells_per_zig

        # north and south border
        ncells = ncells_x

        # figure out how many full and partial zigs we need
        num_full_zigs = ncells // ncpz
        num_leftover_cells = ncells % ncpz

        offset = -abs(zig_undershoot_mm) # in mm

        # very first full zig
        rise_first_full = 1 / float(ncpz-1)

        # full width zig, after the very first
        rise_full = (1 + abs(offset)) / float(ncpz-1)

        # partial zig, made from leftovers
        rise_partial = 0 # 0 means no partials
        if num_leftover_cells > 1:
            rise_partial = (1 + abs(offset)) / float(num_leftover_cells-1)


        #print ncells, ncpz, num_full_zigs, num_leftover_cells, rise_full, brief_text

        # As I have to do 4 passes, I'm wrapping the calculation of the zig "height" into a local function
        def getzigvalue(ci, zig_dist_mm, ncells, ncpz, num_full_zigs, num_leftover_cells, rise_full, rise_partial):
            c_in_zig = ci % ncpz
            #print ncpz, ci, c_in_zig

            # very first zig has to start at 0, not offset
            if ci <= c_in_zig:

                yl = rise_first_full * c_in_zig + 0
                if c_in_zig < ncpz-1:
                    yr = rise_first_full * (c_in_zig+1)
                else:
                    yr = offset # done with first zig, go to offset

            # subsequent zigs, full or partial
            else:

                # full or partial zig
                if ncells - ci > num_leftover_cells:

                    # full cell, go to offset
                    yl = rise_full * c_in_zig + offset
                    if c_in_zig < ncpz-1:
                        yr = rise_full * (c_in_zig+1) + offset
                    else:
                        yr = offset
                else: # partial
                    yl = rise_partial * c_in_zig + offset
                    if c_in_zig < ncpz-1:
                        yr = rise_partial * (c_in_zig+1) + offset
                    else:
                        yr = offset

            # very last cell must set yr as 0
            if ci == ncells-1:
                yr = 0

            #print ci, c_in_zig, yl, yr
            return yl * zig_dist_mm, yr * zig_dist_mm

        # North  and south border
        for ci in range(0, ncells_x):
            yl,yr = getzigvalue(ci,zig_dist_mm, ncells_x, ncpz, num_full_zigs, num_leftover_cells, rise_full, rise_partial)

            # get vertex lists for the 2 quads for the north cell
            nrthcell = self.cells[0,ci]
            topverts = nrthcell.topquad.vl
            botverts = nrthcell.bottomquad.vl

            # note that the vertex order in top or bottom quads are different b/c top has normals up, bottom has normals down

            # order: NEb, NWb, SWb, SEb
            botverts[0].coords[1] += yl  # move y coord up a bit
            botverts[1].coords[1] += yr

            # order: NEt, SEt, SWt, NWt
            topverts[0].coords[1] += yl
            topverts[3].coords[1] += yr



            # get vertex lists for the 2 quads for the south cell
            sthcell = self.cells[-1,ci]
            topverts = sthcell.topquad.vl
            botverts = sthcell.bottomquad.vl


            # order: NEb, NWb, SWb, SEb
            botverts[2].coords[1] += yr  # WTH???? why yr?
            botverts[3].coords[1] += yl

            # order: NEt, SEt, SWt, NWt
            topverts[1].coords[1] += yl
            topverts[2].coords[1] += yr


        # West and east border
        for ci in range(0, ncells_y):
            yl,yr = getzigvalue(ci, zig_dist_mm, ncells_x, ncpz, num_full_zigs, num_leftover_cells, rise_full, rise_partial)


            wcell = self.cells[ci,0]
            topverts = wcell.topquad.vl
            botverts = wcell.bottomquad.vl


            # WTH? why east here?

            # order: NEb, NWb, SWb, SEb
            botverts[0].coords[0] -= yl
            botverts[3].coords[0] -= yr

            # order: NEt, SEt, SWt, NWt
            topverts[0].coords[0] -= yl
            topverts[1].coords[0] -= yr

            # East border
            wcell = self.cells[ci,-1]
            topverts = wcell.topquad.vl
            botverts = wcell.bottomquad.vl


            # WTH? why west here?

            # order: NEb, NWb, SWb, SEb
            botverts[1].coords[0] -= yl
            botverts[2].coords[0] -= yr

            # order: NEt, SEt, SWt, NWt
            topverts[3].coords[0] -= yl
            topverts[2].coords[0] -= yr



    def __str__(self):
        return "TODO: implement __str__() for grid class"

    '''
    def _build_binary_stl_orig(self, facets):
        "in: list of [x,y,z]   out: list of binary STL strings"
        # en.wikipedia.org/wiki/STL_%28file_format%29#Binary_STL
        BINARY_HEADER = "80sI" # up to 80 chars ( do NOT start with the word solid!) + number of faces as UINT32
        BINARY_FACET = "12fH" # 12 32-bit floating-point numbers + 2-byte ("short") unsigned integer ("attribute byte count" -> use 0)
        l = [struct.pack(BINARY_HEADER, b'Binary STL Writer', len(facets))] #  (I)

        for i,facet  in enumerate(facets):
            # prepend normal (0,0,0), pad the end with a unsigned short byte ("attribute byte count")
            # I assume a 0 normal forces the sw reading this to calculate it (Meshlab seems to do this) (???)
            facet = [0,0,0]  + facet + [0]        #print facet
            l.append(struct.pack(BINARY_FACET, *facet))

        return l

    def _build_ascii_stl_orig(self, facets):
        "in: list of [x,y,z]   out: list of ascii STL strings"
        l = ['solid digital_elevation_model'] # digital_elevation_model is the name of the model
        for facet in facets:
            facet = [0,0,0] + facet
            s = ASCII_FACET.format(face=facet) # ASCII string with  12 floats (3 for normal + 3 * 3 for verts )
            #print s
            l.append(s)
        l.append('endsolid digital_elevation_model')
        return l
    '''
    
    def _build_ascii_stl(self, tris, no_normals=False, temp_file=None):
        '''args:
         list of triangles, each a list of 3 verts
         flag for omit normals
         flag for using a temp file   
        returns: either a ascii STL string or the name of the temp file that contains that string
        ''' 
        # template for ascii STL file
        ASCII_FACET ="""facet normal {face[0]:f} {face[1]:f} {face[2]:f}\nouter loop\nvertex {face[3]:f} {face[4]:f} {face[5]:f}\nvertex {face[6]:f} {face[7]:f} {face[8]:f}\nvertex {face[9]:f} {face[10]:f} {face[11]:f}\nendloop\nendfacet\n"""

        s = io.StringIO() # better for growing strings than +=
        s.write('solid digital_elevation_model\n') # digital_elevation_model is the name of the model

        pc_step = int(len(tris)/10) + 1
        progress = 0
        print("assembling ascii stl from", len(tris), "triangles ", temp_file, file=sys.stderr)

        if temp_file != None:
            try:
                fo = open(temp_file, "a") # append
            except Exception as e:
                print("Error opening:", temp_file, e, file=sys.stderr)
                return e

        for i,t  in enumerate(tris):
            
            if t == None: continue # with "tri quads" it's now possible to have None (missing 2. triangle) in the list, ignore those
            tl = get_normal(t) if no_normals == False else [0,0,0]

            for v in t:
                coords = v.get() # get() => list of coords [x,y,z]
                tl.extend(coords) # extend() unpacks that list!
            #print tl
            s.write(ASCII_FACET.format(face=tl))
 
            if i % pc_step == 0:
                progress += 10
                print(progress, "%", file=sys.stderr, end=", ")
                
                if temp_file != None:
                    fo.write(s.getvalue()) # append string to file 
                    s.close()
                    s = io.StringIO() # new string

        s.write('endsolid digital_elevation_model')

        print("\n", file=sys.stderr)

        if temp_file != None: 
            fo.write(s.getvalue()) # append last line and close string and file
            s.close()
            fo.close()  
            return temp_file

        return s.getvalue()

    def _build_binary_stl(self, tris, no_normals=False, temp_file=None):
        '''args:
         list of triangles, each a list of 3 verts
         flag for omit normals
         flag for using a temp file   
        returns: either a binary STL string or the name of the temp file that contains that string
        ''' 

        # en.wikipedia.org/wiki/STL_%28file_format%29#Binary_STL
        BINARY_HEADER = "80sI" # up to 80 chars do NOT start with the word solid + number of faces as UINT32
        BINARY_FACET = "12fH" # 12 32-bit floating-point numbers + 2-byte ("short") unsigned integer ("attribute byte count" -> use 0)
        s = io.BytesIO() # binary "string"
        s.write(struct.pack(BINARY_HEADER, b'Binary STL Writer', len(tris)))

        # stop every 10%
        pc_step = int(len(tris)/10) + 1
        progress = 0
        print("assembling binary stl from", len(tris), "triangles ", temp_file, file=sys.stderr)

        if temp_file != None:
            try:
                fo = open(temp_file, "ab")
            except Exception as e:
                print("Error opening:", temp_file, e, file=sys.stderr)
                return e
                
        for i,t  in enumerate(tris): # go through list of triangles

            if t == None: continue # with "tri quads" it's now possible to have None in the list, ignore those empty half quads
            tl = get_normal(t) if no_normals == False else [0,0,0]
            for v in t:
                coords = v.get() # get() => list of coords [x,y,z]
                tl.extend(coords) # like append() but extend() unpacks that list!
                #print tl
            tl.append(0) # append attribute byte 0
            s.write(struct.pack(BINARY_FACET, *tl)) # append to s

            if i % pc_step == 0:
                progress += 10
                print(progress, "%", file=sys.stderr, end=", ")
                
                if temp_file != None:
                    fo.write(s.getbuffer())   # append (partial) string to file
                    s.close()
                    s = io.BytesIO()

        print("\n", file=sys.stderr)

        if temp_file != None: 
            fo.write(s.getbuffer())   # append (partial) string to file
            s.close()
            fo.close() # close file
            return temp_file

        return s.getbuffer()

    '''
    # version that splits skinny triangles - didn't turn out to be a problem ....
    def make_STLfile_buffer(self, ascii=False, no_bottom=False, temp_file=None):
        """"returns buffer of ASCII or binary STL file from a list of triangles, each triangle must have 9 floats (3 verts, each xyz)
            if no_bottom is True, bottom triangles are omitted
            if temp_file is not None, write STL into it (instead of a buffer) and return it
        """
        # Example: list of 2 triangles
        #[
        # [ 1.0,  1.0,  1.0, # vertex1 xyz
        #  -1.0,  1.0, -1.0, # vertex2 xyz
        #  -1.0, -1.0,  1.0] # vertex3 xyz
        # [ 1.0,  1.0,  1.0,
        #  -1.0, -1.0,  1.0,
        #   1.0, -1.0, -1.0]
        #]
        # Normal for each facet is set to 0,0,0

        triangles = [] # list of triangles

        # number of cells in x and y     grid is cells[y,x]
        ncells_x = self.cells.shape[1]
        ncells_y = self.cells.shape[0]

        # go through all cells, get all its quads and split into triangles
        for ix in range(0, ncells_x):
          for iy in range(0, ncells_y):
            cell = self.cells[iy,ix] # get cell from 2D array of cells (grid)

            if cell != None:
                #print "cell", ix, iy

                # list of top/bottom quads for this cell,
                if no_bottom == False:
                    quads = [cell.topquad, cell.bottomquad]
                else:
                    quads = [cell.topquad] # no bottom quads, only top

                # get tris for top and bottom
                for q in quads:
                    tl = q.get_triangles() # triangle list
                    triangles.append(tl[0])
                    triangles.append(tl[1])

                # add tris for border quads     cell.borders is a dict with S E W N as keys and a quad as value (if there's a border in that direction, False, otherwise)
                for k in cell.borders.keys():
                    border_quad = cell.borders[k]
                    if  border_quad != False:
                        #print k,
                        # run a check if wall is too skinny, this will set an quad internal value for how much to subdivide
                        # the subdivision will happen later when we ask for the skinny wall's triangles
                        # we need the direction (k) b/c the order of verts is different for n/s vs e/w!
                        border_quad.check_if_too_skinny(k)
                        #print border_quad, border_quad.subdivide_by
                        tl = border_quad.get_triangles(k) # triangle list
                        for t in tl:
                            triangles.append(t)

        #for n,t in enumerate(triangles): print n, t[0], t[1], t[2]

        buf = None
        if ascii:
            buf_as_list = self._build_ascii_stl(triangles)
            buf = "\n".join(buf_as_list).encode("UTF-8") # single utf8 string
        else:
            buf_as_list = self._build_binary_stl(triangles)
            buf = b"".join(buf_as_list)  # single "binary string"/buffer

        #print len(buf)

        if temp_file ==  None: return buf

        # Write string into temp file and return it
        temp_file.write(buf)
        return temp_file
    '''


    def make_STLfile_buffer(self, tile_info, ascii=False, temp_file=None):
        """"returns buffer of ASCII or binary STL file from a list of triangles, each triangle must have 9 floats (3 verts, each xyz)
            if no_bottom is True, bottom triangles are omitted, otherwise 2 triangles are added
            if no_bottom is False but has_nan is True, a "detailed" buttom is used
            if temp_file is not None, write STL into it (instead of a buffer) and return it
            if no_normals is True, all normals are 0,0,0, which makes this process subestantially faster
        """
        # Example: list of 2 triangles
        #[
        # [ 1.0,  1.0,  1.0, # vertex1 xyz
        #  -1.0,  1.0, -1.0, # vertex2 xyz
        #  -1.0, -1.0,  1.0] # vertex3 xyz
        # [ 1.0,  1.0,  1.0,
        #  -1.0, -1.0,  1.0,
        #   1.0, -1.0, -1.0]
        #]
        # Normal for each facet is set to 0,0,0

        triangles = [] # list of triangles
        no_bottom = tile_info["no_bottom"]
        no_normals = tile_info["no_normals"]
        have_nan = tile_info["have_nan"]
        have_bottom_array = tile_info["have_bottom_array"]  

        # number of cells in x and y    grid is cells[y,x]
        ncells_x = self.cells.shape[1]
        ncells_y = self.cells.shape[0]

        # go through all cells, get all its quads and split into triangles
        for ix in range(0, ncells_x):
          for iy in range(0, ncells_y):
            cell = self.cells[iy,ix] # get cell from 2D array of cells (grid)
        
            if cell != None: # None means raster value was NaN at that location

                # list of quads for this cell,
                if no_bottom == False and (have_nan or have_bottom_array): #  
                    quads = [cell.topquad, cell.bottomquad]
                else:
                    quads = [cell.topquad ] # no bottom quads, only top
 
                # add border quads if we have any (False means no border quad) 
                for k in list(cell.borders.keys()): 
                    if cell.borders[k] != False: quads.append(cell.borders[k])
                    # TODO? the tris for these quads can become very skinny, should be subdivided into more quads to keep the angles high enough
                
                for q in quads: 
                    t0,t1 = q.get_triangles()
                    triangles.append(t0)
                    if t1  != None: # for a tri quadcell t1 is None
                        triangles.append(t1)
        
        # Do we need to add a 2-triangle  bottom?
        add_simple_bottom = True 
        
        # We don't have bottom tris but that's OK as we don't won't any (no_bottom option was set)
        if no_bottom == True: add_simple_bottom = False # 
        
        # With a NaN (masked) top array we already have the corresponding full bottom
        if tile_info["have_nan"] == True: add_simple_bottom = False 
        
        # with a bottom image, we also already have a full bottom
        if tile_info["bottom_image"] != None: add_simple_bottom = False

         # obj files currently don't support simple bottoms
        if tile_info["fileformat"] == 'obj': add_simple_bottom = False

        #  Add 2 triangles based on the corners of the tile
        if add_simple_bottom:
            v0 = vertex(tile_info["W"], tile_info["S"], 0, self.vi)
            v1 = vertex(tile_info["E"], tile_info["S"], 0, self.vi)
            v2 = vertex(tile_info["E"], tile_info["N"], 0, self.vi)
            v3 = vertex(tile_info["W"], tile_info["N"], 0, self.vi)
            #for v in (v0, v1, v2, v3): print(v)
 
            t0 = (v0, v2, v1) #A
            triangles.append(t0)
            
            t1 = (v0, v3, v2) #A
            triangles.append(t1)    
        #for t in triangles: print t # debug

        # Write triangles into file
        if ascii:
            buf = self._build_ascii_stl(triangles, no_normals, temp_file)
        else:

            # profiling
            '''
            import cProfile, pstats, io
            from pstats import SortKey
            pr = cProfile.Profile()
            pr.enable()
            '''

            #buf_as_list = self._build_binary_stl(triangles, no_normals)
            #buf = b"".join(buf_as_list)  # single "binary string"/buffer

            buf = self._build_binary_stl(triangles, no_normals, temp_file)

            '''
            pr.disable()
            s = io.StringIO()
            sortby = SortKey.CUMULATIVE
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats()
            print(s.getvalue())
            '''

        #print len(buf)
        if temp_file ==  None:
            return buf
        else:
            return temp_file




    def make_OBJfile_buffer(self, no_bottom=False, temp_file=None, no_normals=True):
        """returns buffer of OBJ file, creates a list of triangles and a list of indexed x,y,z vertices
        if no_bottom is True, bottom triangles are omitted
        if temp_file is not None, open it and write OBJ into it (instead of a buffer) and close it,
        return temp_file or exception
        no_normals is not used for now, no normal information is generated

        mtllib dontcare.mtl
        g vert
        v 0.00 0.00 -4938.15
        v 9751.96 0.00 -4944.11
        v 9751.96 9686.67 -4947.28
        v 0.00 9686.67 -4963.42
        g tris
        usemtl initialShadingGroup
        f 1 2 3
        f 3 4 1
        """
        assert self.vi != -1, "make_OBJfile_buffer: need grid with vertex indices!"

        # make a stream s, either on disk or memory and append to it via write()
        if temp_file != None:
            try:
                s = open(temp_file, "a") # append
            except Exception as e:
                print("Error opening:", temp_file, e, file=sys.stderr)
                return e
        else:
            s = io.StringIO()


        s.write("g vert\n")
        for v in list(self.vi.keys()): # self.vi is a dict of vertex indices
            #vstr = "v %f %f %f #%d" % (v[0], v[1], v[2], i+1) # putting the vert index behind a comment makes some programs crash when loading the obj file!
            vstr = f"v {v[0]}, {v[1]}, {v[2]}\n"
            s.write(vstr)

        s.write("\ng tris\n")

        # number of cells in x and y     grid is cells[y,x]
        ncells_x = self.cells.shape[1]
        ncells_y = self.cells.shape[0]

        # go through all cells, get all its quads and split into triangles
        for ix in range(0, ncells_x):
          for iy in range(0, ncells_y):
            cell = self.cells[iy,ix] # get cell from 2D array of cells (grid)

            if cell != None:
                # list of quads for this cell,
                if no_bottom == False:
                    quads = [cell.topquad, cell.bottomquad]
                else:
                    quads = [cell.topquad] # no bottom quads, only top

                for k in list(cell.borders.keys()): # plus get border quads if we have any
                    if cell.borders[k] != False: quads.append(cell.borders[k])
                for q in quads:
                    t0i,t1i = q.get_triangles_with_indexed_verts()

                    for f in (t0i, t1i): # output the 2 facets (triangles)
                        if f == None: continue # for "tri quads" one tri is None
                        fstr = "f %d %d %d\n" % (f[0]+1, f[1]+1, f[2]+1) # OBJ indices start at 1!
                        #buf_as_list.append(fstr)
                        s.write(fstr)
                del cell


        if temp_file ==  None:
            return s.getvalue() # return stream as string
        else:
            s.close()
            return temp_file  



# MAIN  (left this in so I can test stuff ...)

#@profile # https://pypi.org/project/memory-profiler/
def main():
    nn = np.nan
    """
    top = np.array([[11,12,13,14],
                    [21,nn,nn,24],
                    [31,nn,nn,34],
                    [41,42,43,44],
                   ])

    top = np.array([[11,12,13],
                     [21,nn,23],
                     [31,32,33],
                    ])

    top = np.array([[np.nan, np.nan],
                    [np.nan, np.nan],
                    [np.nan, np.nan],
                    [1,1],
                   ])
    top = np.array([[0.3,0.5,0.4],
                    [0.4,np.nan,0.6],
                    [0.3,0.6,0.7],
                   ])

    top = np.array([[1],
                      ])

    top = np.array([[1.0,1.1, 1.2],
                    [1.4,1.2, 1.3],
                    [1.5,2.6, 1.0],
                    [1.2,1.6, 1.7],
                   ])
    
    top =  np.array([
                        [nn, nn, nn, 11, 11, nn, nn],
                        [nn, nn, 17, 22, 24, nn, nn],
                        [nn, 13, 33, 44, 33, 24, nn],                     
                        [11, 22, 55, 70, 25, 30, nn],
                        [14, 17, 33, 39, nn, 22, 12],
                        [nn, 10, 23, 10, nn, 10, nn],   
                        [nn, nn, 11,  6, nn, nn, nn],                     
                     ])
    
    top =  np.array([
                    [10, 10, 10, 10, 10, 10, 10],
                    [10, 10, 10, 10, 10, 10, 10],
                    [10, 10, 10, 10, 10, 10, 10],                     
                    [10, 10, 10, 100, 10, 10, 10],
                    [10, 10, 10, 10, 10, 10, 10],
                    [10, 10, 10, 10, 10, 10, 10],  
                    [10, 10, 10, 10, 10, 10, 10],                    
                    ])
    
    top =  np.array([ [nn, nn, 11],
                      [11, nn, nn],
                      [11, 11, nn],
                 ])
    
    top =  np.array([
                         [ 1, 5, 10, 50, 20, 10, 1],
                         [ 1, 10, 10, 50, 20, 10, 2],
                         [ 1, 11, 150, 30, 30, 10, 5],
                         [ 1, 23, 100, 40, 20, 10, 2 ],
                         [ 1, 50, 10, 10, 20, 10 , 1 ],

                   ])
    top = np.array([ [1]])
    """

    top =  np.array([ [2, 3],
                      [3, 2],
                 ])
    
 
    #top = np.ones( (200, 200) , dtype=np.int64)

    #bottom = np.zeros((4, 3)) # num along x, num along y
    top = top.astype(float)
    print(top)
    #print bottom

    """
    import matplotlib.pyplot as plt
    #plt.ion()
    fig = plt.figure(figsize=(7,10))
    npim = top
    imgplot = plt.imshow(npim, aspect=u"equal", interpolation=u"none")
    cmap_name = 'nipy_spectral' # gist_earth or terrain or nipy_spectral
    imgplot.set_cmap(cmap_name)
    #a = fig.add_axes()
    #plt.title(DEM_name + " " + str(center))
    plt.colorbar(orientation="horizontal")
    plt.show()
    """


    tile_info_dict = {
        #"scale"  : 10000, # horizontal scale number, defines the size of the model (= 3D map): 1000 => 1m (real) = 1000m in model
        "scale"  : 1, 
        "pixel_mm" : 1, # lateral (x/y) size of a pixel in mm
        "max_elev" : np.nanmax(top), # tilewide minimum/maximum elevation (in meter), either int or float, depending on raster
        "min_elev" : np.nanmin(top),
        "z_scale" :  5,     # z (vertical) scale (elevation exageration) factor, float
        "tile_no_x": 1, # current tile number in x, int, starting with 1, at upper left corner
        "tile_no_y": 1,
        "ntilesx": 1,
        "ntilesy": 1,
        "tile_centered" : False, # True: each tile's center is 0/0, False: global (all-tile) 0/0
        "fileformat": "stlb",  # folder/zip file name for all tiles
        #"fileformat": "obj",
        "base_thickness_mm": 10, # thickness between bottom and lowest elevation, NOT including the bottom relief.
        "tile_width": 100,
        "use_geo_coords": None,
        "no_bottom": False,
        "no_normals": True,
        "CPU_cores_to_use" : 1,
    }

    whratio = top.shape[0] / float(top.shape[1])
    tile_info_dict["tile_height"] = tile_info_dict["tile_width"]  * whratio

    top = np.pad(top, (1,1), 'edge')
    g = grid(top, None, tile_info_dict)



    #b = g.make_STLfile_buffer(ascii=True, no_normals=True, temp_file="STLtest_asc6.stl")
    #b = g.make_STLfile_buffer(ascii=False, no_normals=False, temp_file="STLtest_new_b3.stl")
    b = g.make_STLfile_buffer(tile_info_dict, ascii=False, temp_file="STLtest.stl")
    #f = open("STLtest_new.stl", 'wb');f.write(b);f.close()

    #b = g.make_OBJfile_buffer(no_bottom=False, temp_file="OBJtest2.obj", no_normals=False)
    print("done")


if __name__ == "__main__":
    main()

