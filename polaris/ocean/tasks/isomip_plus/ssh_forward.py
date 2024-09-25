from polaris.ocean.ice_shelf.ssh_forward import (
    SshForward as IceShelfSshForward,
)


class SshForward(IceShelfSshForward):
    """
    A step for performing forward ocean component runs as part of ssh
    adjustment.
    """

    def compute_cell_count(self):
        """
        Compute the approximate number of cells in the mesh, used to constrain
        resources

        Returns
        -------
        cell_count : int or None
            The approximate number of cells in the mesh
        """
        resolution = self.min_resolution
        cell_count = int(45e3 / resolution**2)
        return cell_count
