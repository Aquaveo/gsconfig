import unittest
from geoserver.catalog import Catalog
from geoserver.util import shapefile_and_friends

class CatalogTests(unittest.TestCase):
  def setUp(self):
    self.cat = Catalog("http://localhost:8080/geoserver/rest")


  def testWorkspaces(self):
    self.assertEqual(7, len(self.cat.get_workspaces()))
    self.assertEqual("cite", self.cat.get_default_workspace().name)
    self.assertEqual("topp", self.cat.get_workspace("topp").name)


  def testStores(self):
    topp = self.cat.get_workspace("topp")
    sf = self.cat.get_workspace("sf")
    self.assertEqual(9, len(self.cat.get_stores()))
    self.assertEqual(2, len(self.cat.get_stores(topp)))
    self.assertEqual(2, len(self.cat.get_stores(sf)))
    self.assertEqual("states_shapefile", self.cat.get_store("states_shapefile", topp).name)
    self.assertEqual("states_shapefile", self.cat.get_store("states_shapefile").name)
    self.assertEqual("sfdem", self.cat.get_store("sfdem", sf).name)
    self.assertEqual("sfdem", self.cat.get_store("sfdem").name)

  
  def testResources(self):
    topp = self.cat.get_workspace("topp")
    sf = self.cat.get_workspace("sf")
    states = self.cat.get_store("states_shapefile", topp)
    sfdem = self.cat.get_store("sfdem", sf)
    self.assertEqual(19, len(self.cat.get_resources()))
    self.assertEqual(1, len(self.cat.get_resources(states)))
    self.assertEqual(5, len(self.cat.get_resources(workspace=topp)))
    self.assertEqual(1, len(self.cat.get_resources(sfdem)))
    self.assertEqual(6, len(self.cat.get_resources(workspace=sf)))

    self.assertEqual("states", self.cat.get_resource("states", states).name)
    self.assertEqual("states", self.cat.get_resource("states", workspace=topp).name)
    self.assertEqual("states", self.cat.get_resource("states").name)
    states = self.cat.get_resource("states")

    fields = [
        states.title,
        states.abstract,
        states.native_bbox,
        states.latlon_bbox,
        states.projection,
        states.projection_policy
    ]

    self.assertFalse(None in fields, str(fields))
    self.assertFalse(len(states.keywords) == 0)
    self.assertFalse(len(states.attributes) == 0)
    self.assertTrue(states.enabled)

    self.assertEqual("sfdem", self.cat.get_resource("sfdem", sfdem).name)
    self.assertEqual("sfdem", self.cat.get_resource("sfdem", workspace=sf).name)
    self.assertEqual("sfdem", self.cat.get_resource("sfdem").name)


  def testLayers(self):

    expected = set(["Arc_Sample", "Pk50095", "Img_Sample", "mosaic", "sfdem",
      "bugsites", "restricted", "streams", "archsites", "roads",
      "tasmania_roads", "tasmania_water_bodies", "tasmania_state_boundaries",
      "tasmania_cities", "states", "poly_landmarks", "tiger_roads", "poi",
      "giant_polygon"
    ])
    actual = set(l.name for l in self.cat.get_layers())
    missing = expected - actual
    extras = actual - expected
    message = "Actual layer list did not match expected! (Extras: %s) (Missing: %s)" % (extras, missing)
    self.assert_(len(expected ^ actual) == 0, message)

    self.assert_("states", self.cat.get_layer("states").name)


  def testStyles(self):
    self.assertEqual(22, len(self.cat.get_styles()))
    self.assertEqual("population", self.cat.get_style("population").name)


class ModifyingTests(unittest.TestCase):
  def setUp(self):
    self.cat = Catalog("http://localhost:8080/geoserver/rest")


  def testFeatureTypeSave(self):
    # test saving round trip
    rs = self.cat.get_resource("bugsites")
    old_abstract = rs.abstract
    new_abstract = "Not the original abstract"

    # Change abstract on server
    rs.abstract = new_abstract
    self.cat.save(rs)
    rs = self.cat.get_resource("bugsites")
    self.assertEqual(new_abstract, rs.abstract)

    # Restore abstract
    rs.abstract = old_abstract
    self.cat.save(rs)
    rs = self.cat.get_resource("bugsites")
    self.assertEqual(old_abstract, rs.abstract)


  def testCoverageSave(self):
    # test saving round trip
    rs = self.cat.get_resource("Arc_Sample")
    old_abstract = rs.abstract
    new_abstract = "Not the original abstract"

    # # Change abstract on server
    rs.abstract = new_abstract
    self.cat.save(rs)
    rs = self.cat.get_resource("Arc_Sample")
    self.assertEqual(new_abstract, rs.abstract)

    # Restore abstract
    rs.abstract = old_abstract
    self.cat.save(rs)
    rs = self.cat.get_resource("Arc_Sample")
    self.assertEqual(old_abstract, rs.abstract)

  def testFeatureTypeCreate(self):
    shapefile_plus_boxcars = shapefile_and_friends("test/data/states")
    expected = {
      'shp': 'test/data/states.shp',
      'shx': 'test/data/states.shx',
      'dbf': 'test/data/states.dbf',
      'prj': 'test/data/states.prj'
    }

    self.assertEqual(len(expected), len(shapefile_plus_boxcars))
    for k, v in expected.iteritems():
      self.assertEqual(v, shapefile_plus_boxcars[k])
 
    sf = self.cat.get_workspace("sf")
    ft = self.cat.create_featurestore("states_test", shapefile_plus_boxcars, sf)

    # Will throw exception if the resource isn't found; no explicit assertion
    # needed
    self.cat.get_resource("states_test", workspace=sf)

  def testCoverageCreate(self):
    tiffdata = {
      'tiff': 'test/data/Pk50095.tif',
      'tfw':  'test/data/Pk50095.tfw',
      'prj':  'test/data/Pk50095.prj'
    }

    sf = self.cat.get_workspace("sf")
    ft = self.cat.create_coveragestore("Pk50095", tiffdata, sf)

    # Will throw exception if the resource isn't found; no explicit assertion
    # needed
    self.cat.get_resource("Pk50095", workspace=sf) 

  def testLayerSave(self):
    # test saving round trip
    lyr = self.cat.get_layer("states")
    old_attribution = lyr.attribution
    new_attribution = "Not the original attribution"

    # change attribution on server
    lyr.attribution = new_attribution
    self.cat.save(lyr)
    lyr = self.cat.get_layer("states")
    self.assertEqual(new_attribution, lyr.attribution)

    # Restore attribution
    lyr.attribution = old_attribution
    self.cat.save(lyr)
    self.assertEqual(old_attribution, lyr.attribution)


  def testLayerDelete(self):
    lyr = self.cat.get_layer("states") 
    self.cat.delete(lyr)
    self.assert_(self.cat.get_layer("states") is None)


  def testWorkspaceDelete(self): 
    pass 

  def testFeatureTypeDelete(self):
    pass

  def testCoverageDelete(self):
    pass

  def testDataStoreDelete(self):
    pass

if __name__ == "__main__":
  unittest.main()
