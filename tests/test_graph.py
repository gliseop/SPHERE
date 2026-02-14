"""Тесты социального графа."""

from magistry_sim.graph import SocialGraph


class TestSocialGraph:
    def test_add_agent(self):
        g = SocialGraph()
        g.add_agent("a")
        g.add_agent("b")
        assert g.graph.has_node("a")
        assert g.graph.has_node("b")

    def test_add_connection(self):
        g = SocialGraph()
        g.add_connection("a", "b", relation="коллеги", strength=2.0)
        assert g.get_strength("a", "b") == 2.0

    def test_strengthen(self):
        g = SocialGraph()
        g.add_connection("a", "b", strength=1.0)
        g.strengthen("a", "b", delta=0.5)
        assert g.get_strength("a", "b") == 1.5

    def test_strengthen_new_edge(self):
        g = SocialGraph()
        g.add_agent("a")
        g.add_agent("b")
        g.strengthen("a", "b", delta=0.3)
        assert g.get_strength("a", "b") == 0.3

    def test_get_connections(self):
        g = SocialGraph()
        g.add_connection("a", "b", relation="друзья")
        g.add_connection("a", "c", relation="коллеги")
        conns = g.get_connections("a")
        assert len(conns) == 2

    def test_no_connections(self):
        g = SocialGraph()
        g.add_agent("a")
        assert g.get_connections("a") == []
        assert g.get_connections("nonexistent") == []

    def test_get_suspicious_edges(self):
        g = SocialGraph()
        g.add_connection("a", "b", strength=1.0)
        g.add_connection("a", "c", strength=5.0)
        suspicious = g.get_suspicious_edges(threshold=3.0)
        assert len(suspicious) == 1
        assert suspicious[0][2] == 5.0

    def test_density(self):
        g = SocialGraph()
        g.add_agent("a")
        g.add_agent("b")
        g.add_agent("c")
        assert g.density() == 0.0
        g.add_connection("a", "b")
        assert g.density() > 0.0
